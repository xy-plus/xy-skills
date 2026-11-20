"""Remove whole expired, unused items from explicitly supplied Linux roots."""

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import glob
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import uuid


TRASH_NAME = ".runs-cleaner-trash"
DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def is_expired(path, cutoff):
    """lstat every entry, stopping at the first new or unreadable entry."""
    pending = [path]
    try:
        while pending:
            entry = pending.pop()
            details = entry.lstat()
            if details.st_mtime >= cutoff or details.st_ctime >= cutoff:
                return False
            if stat.S_ISDIR(details.st_mode):
                pending.extend(entry.iterdir())
    except OSError:
        return False
    return True


def expand_roots(patterns, errors):
    roots = {}
    for pattern in patterns:
        absolute = os.path.abspath(os.path.expanduser(pattern))
        matches = glob.glob(absolute)
        if not matches:
            print(f"warning: root does not exist: {absolute}", file=errors)
        for match in matches:
            root = Path(match)
            if any(part.is_symlink() for part in (root, *root.parents)):
                print(f"warning: root crosses a symlink: {root}", file=errors)
            elif root.is_dir():
                roots[root] = root.lstat()
            else:
                print(f"warning: root is not a directory: {root}", file=errors)
    return {root: roots[root] for root in sorted(roots, key=lambda root: (len(root.parts), str(root)))}


def process_paths(process):
    """Unreadable references of non-dumpable processes are deliberately skipped."""
    for name in ("cwd", "root", "exe"):
        try:
            yield os.readlink(process / name)
        except OSError:
            pass
    try:
        descriptors = list((process / "fd").iterdir())
    except OSError:
        descriptors = []
    for descriptor in descriptors:
        try:
            yield os.readlink(descriptor)
        except OSError:
            pass
    try:
        with (process / "maps").open(encoding="utf-8", errors="surrogateescape") as mappings:
            for line in mappings:
                fields = line.rstrip("\n").split(None, 5)
                if len(fields) == 6 and fields[5].startswith("/"):
                    yield fields[5]
    except OSError:
        pass


def is_in_use(item):
    path = str(item)
    command_path = re.compile(r"(?<![\w/.\-])" + re.escape(path) + r"(?=[/\s\x00]|$)")
    user = os.getuid()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        if int(process.name) == os.getpid():
            continue
        try:
            # /proc/<pid> ownership changes for non-dumpable processes. The
            # real UID in status remains readable, so do not use stat.st_uid.
            status = (process / "status").read_bytes()
            owner = next(line.split()[1] for line in status.splitlines()
                         if line.startswith(b"Uid:"))
            if int(owner) != user:
                continue
        except (OSError, StopIteration, ValueError):
            continue
        try:
            command = os.fsdecode((process / "cmdline").read_bytes())
            if command_path.search(command):
                return True
        except OSError:
            pass
        for reference in process_paths(process):
            # A real name may itself end with " (deleted)"; compare the raw
            # reference first, then the kernel-appended marker version.
            if reference == path or reference.startswith(path + "/"):
                return True
            reference = reference.removesuffix(" (deleted)")
            if reference == path or reference.startswith(path + "/"):
                return True
    return False


@contextmanager
def checked_directory(root, expected):
    """Open every path component without following links, then verify identity."""
    descriptor = os.open(root.anchor, DIRECTORY_OPEN_FLAGS)
    try:
        for component in root.parts[1:]:
            child = os.open(component, DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if not os.path.samestat(expected, os.fstat(descriptor)):
            raise OSError(f"root changed since expansion: {root}")
        yield descriptor
    finally:
        os.close(descriptor)


def remove_item(path):
    if stat.S_ISDIR(path.lstat().st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


class CleanerError(ValueError):
    """A configured path or explicitly selected target violates the cleanup boundary."""


def recover_trash(outer, expected, errors):
    trash = outer / TRASH_NAME
    status = 0
    try:
        with checked_directory(outer, expected) as outer_descriptor, ExitStack() as handles:
            fcntl.flock(outer_descriptor, fcntl.LOCK_EX)
            try:
                trash_descriptor = os.open(TRASH_NAME, DIRECTORY_OPEN_FLAGS,
                                           dir_fd=outer_descriptor)
            except FileNotFoundError:
                return 0
            handles.callback(os.close, trash_descriptor)
            for name in sorted(os.listdir(trash_descriptor)):
                leftover = Path(f"/proc/self/fd/{trash_descriptor}") / name
                try:
                    remove_item(leftover)
                except OSError as error:
                    print(f"error: {trash / name}: {error}", file=errors)
                    status = 1
    except OSError as error:
        print(f"error: {trash}: {error}", file=errors)
        status = 1
    return status


def delete_candidate(roots, outer, root, name, expected_item, cutoff, required,
                     output, errors):
    item = root / name
    try:
        with checked_directory(outer, roots[outer]) as outer_descriptor:
            fcntl.flock(outer_descriptor, fcntl.LOCK_EX)
            with ExitStack() as handles:
                with checked_directory(root, roots[root]) as root_descriptor:
                    current = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
                    if not os.path.samestat(expected_item, current):
                        if required:
                            raise CleanerError("点名目标已被替换")
                        return 0
                    entry = Path(f"/proc/self/fd/{root_descriptor}") / name
                    if cutoff is not None and not is_expired(entry, cutoff):
                        if required:
                            raise CleanerError("点名目标的年龄状态已变化")
                        return 0
                    if required and not entry.resolve(strict=True).is_relative_to(outer):
                        raise CleanerError("软链真实目标不在 RUNS_ROOT 内")
                    if is_in_use(item):
                        if required:
                            raise CleanerError("点名目标仍被本用户进程使用")
                        return 0
                    try:
                        os.mkdir(TRASH_NAME, dir_fd=outer_descriptor)
                    except FileExistsError:
                        pass
                    trash_descriptor = os.open(TRASH_NAME, DIRECTORY_OPEN_FLAGS,
                                               dir_fd=outer_descriptor)
                    handles.callback(os.close, trash_descriptor)
                    destination = Path(f"/proc/self/fd/{trash_descriptor}") / uuid.uuid4().hex
                    while os.path.lexists(destination):
                        destination = destination.parent / uuid.uuid4().hex
                    os.rename(name, destination.name, src_dir_fd=root_descriptor,
                              dst_dir_fd=trash_descriptor)
                remove_item(destination)
                print(json.dumps(str(item), ensure_ascii=True), file=output)
    except (OSError, CleanerError) as error:
        print(f"error: {item}: {error}", file=errors)
        return 1
    return 0


def clean_roots(patterns, mode, cutoff, output, errors):
    """Run expiry checks around two locked transactions: trash recovery and one-item deletion."""
    status = 0
    roots = expand_roots(patterns, errors)
    outer_roots = {root: next(outer for outer in roots if root.is_relative_to(outer))
                   for root in roots}
    if mode != "list":
        for outer in dict.fromkeys(outer_roots.values()):
            status |= recover_trash(outer, roots[outer], errors)
    for root in roots:
        # A previously removed ancestor may have contained this nested root.
        if not os.path.lexists(root):
            continue
        outer = outer_roots[root]
        try:
            with checked_directory(root, roots[root]) as root_descriptor:
                for name in sorted(os.listdir(root_descriptor)):
                    item = root / name
                    if name in (TRASH_NAME, ".runs-cleaner-previewed"):
                        continue
                    if not is_expired(Path(f"/proc/self/fd/{root_descriptor}") / name, cutoff):
                        continue
                    if mode == "list":
                        if not is_in_use(item):
                            print(json.dumps(str(item), ensure_ascii=True), file=output)
                        continue
                    expected_item = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
                    status |= delete_candidate(roots, outer, root, name, expected_item,
                                               cutoff, False, output, errors)
        except OSError as error:
            print(f"error: {root}: {error}", file=errors)
            status = 1
    return status


def read_runs_root(settings_path):
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CleanerError(f"无法读取用户设置 {settings_path}: {error}") from error
    environment = settings.get("env") if isinstance(settings, dict) else None
    runs = environment.get("RUNS_ROOT") if isinstance(environment, dict) else None
    if not isinstance(runs, str) or not runs or not os.path.isabs(runs):
        raise CleanerError("用户设置 env.RUNS_ROOT 必须是非空绝对路径")
    return runs


def configured_patterns(runs_value, additional_roots):
    runs = glob.escape(runs_value)
    return [*additional_roots, runs, runs + "/-*", runs + "/-*/xy-goal-inbox"]


def validate_obsolete_target(root_value, target_value):
    if (not os.path.isabs(root_value) or os.path.normpath(root_value) != root_value
            or "\x00" in root_value):
        raise CleanerError("RUNS_ROOT 必须是字面绝对路径")
    if (not os.path.isabs(target_value) or os.path.normpath(target_value) != target_value
            or "\x00" in target_value):
        raise CleanerError("目标必须是字面绝对路径")
    root, target = Path(root_value), Path(target_value)
    try:
        root_info = root.lstat()
        if not stat.S_ISDIR(root_info.st_mode) or root.resolve(strict=True) != root:
            raise CleanerError(f"RUNS_ROOT 必须是无软链的真实目录：{root}")
        relative = target.relative_to(root)
    except (OSError, ValueError) as error:
        raise CleanerError(f"目标不在 RUNS_ROOT 下或路径无法验证：{target}") from error
    if (len(relative.parts) < 2 or not relative.parts[0].startswith("-")
            or relative.parts[1] in ("xy-goal-inbox", TRASH_NAME)
            or TRASH_NAME in relative.parts):
        raise CleanerError("目标必须位于 RUNS_ROOT 下某项目的一件事目录或其内容中")
    project = root / relative.parts[0]
    item_root = root / relative.parts[0] / relative.parts[1]
    try:
        if not stat.S_ISDIR(project.lstat().st_mode):
            raise CleanerError(f"项目目录必须是真实目录：{project}")
        if not stat.S_ISDIR(item_root.lstat().st_mode):
            raise CleanerError(f"事项目录必须是真实目录：{item_root}")
        if not os.path.lexists(target):
            raise CleanerError(f"点名目标不存在：{target}")
        target_info = target.lstat()
        current = target.parent
        while current != root:
            if current.resolve(strict=True) != current or not stat.S_ISDIR(current.lstat().st_mode):
                raise CleanerError(f"目标路径的上级目录经过软链或不是目录：{current}")
            current = current.parent
        if current.resolve(strict=True) != current:
            raise CleanerError(f"目标路径经过软链：{current}")
        resolved_target = target.resolve(strict=True)
        if not resolved_target.is_relative_to(root):
            raise CleanerError("软链真实目标不在 RUNS_ROOT 内")
    except (OSError, RuntimeError) as error:
        raise CleanerError(f"目标路径无法安全验证：{target}: {error}") from error
    return root, target, item_root, root_info, target_info, resolved_target


def find_systemd_reference(directory, paths):
    if not os.path.lexists(directory):
        return None
    try:
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise CleanerError(f"systemd 用户单元目录不是实际目录：{directory}")
        for entry in sorted(directory.rglob("*"), key=lambda value: value.as_posix()):
            if entry.is_dir() and not entry.is_symlink():
                continue
            if not (entry.is_file() or entry.is_symlink()):
                continue
            content = entry.read_bytes().decode("utf-8", errors="surrogateescape")
            if any(str(path) in content for path in paths):
                return entry
    except OSError as error:
        raise CleanerError(f"无法完整检查 systemd 用户单元：{error}") from error
    return None


def find_runner_reference(item_root, target, resolved_target):
    task_directory = item_root / "sub-agent-runner" / "tasks"
    if not os.path.lexists(task_directory):
        return None
    try:
        if not stat.S_ISDIR(task_directory.lstat().st_mode):
            raise CleanerError(f"sub-agent-runner/tasks 不是实际目录：{task_directory}")
        for metadata_path in sorted(task_directory.glob("*.json")):
            if metadata_path.is_symlink() or not metadata_path.is_file():
                raise CleanerError(f"sub-agent-runner 任务元数据不是普通文件：{metadata_path}")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            workdir = metadata.get("dir") if isinstance(metadata, dict) else None
            if not isinstance(workdir, str) or not os.path.isabs(workdir):
                raise CleanerError(f"sub-agent-runner 任务缺少有效 dir：{metadata_path}")
            workdir_path = Path(workdir)
            resolved_workdir = workdir_path.resolve(strict=False)
            if (workdir_path == target or workdir_path.is_relative_to(target)
                    or resolved_workdir == resolved_target
                    or resolved_workdir.is_relative_to(resolved_target)):
                return metadata_path
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        if isinstance(error, CleanerError):
            raise
        raise CleanerError(f"无法完整检查 sub-agent-runner 任务：{error}") from error
    return None


def delete_obsolete(target_value, other_sessions_checked, settings_path,
                    units_directory, output, errors):
    if not other_sessions_checked:
        raise CleanerError("删除前必须显式确认已检查其他会话依赖")
    root, target, item_root, root_info, target_info, resolved_target = validate_obsolete_target(
        read_runs_root(settings_path), target_value)
    if is_in_use(target) or (resolved_target != target and is_in_use(resolved_target)):
        raise CleanerError(f"目标仍被本用户进程使用：{target}")
    unit = find_systemd_reference(units_directory, (target, resolved_target))
    if unit is not None:
        raise CleanerError(f"systemd 用户单元引用目标：{unit}")
    task = find_runner_reference(item_root, target, resolved_target)
    if task is not None:
        raise CleanerError(f"sub-agent-runner 任务可用 --dir 续跑目标：{task}")

    parent_info = target.parent.lstat()
    roots = {root: root_info, target.parent: parent_info}
    if recover_trash(root, root_info, errors) != 0:
        return 1
    return delete_candidate(roots, root, target.parent, target.name, target_info,
                           None, True, output, errors)


def configured_cleaner(arguments):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("list", "delete"))
    parser.add_argument("--additional-root", action="append", required=True,
                        help="额外的清理根目录模式；可重复")
    options = parser.parse_args(arguments)
    settings_path = Path.home() / ".claude" / "settings.json"
    try:
        runs = read_runs_root(settings_path)
        patterns = configured_patterns(runs, options.additional_root)
        if options.mode == "delete":
            root = Path(runs)
            marker = root / ".runs-cleaner-previewed"
            try:
                marker_info = marker.lstat()
            except FileNotFoundError as error:
                raise CleanerError(f"RUNS_ROOT preview marker is missing: {marker}") from error
            if not stat.S_ISREG(marker_info.st_mode):
                raise CleanerError(f"RUNS_ROOT preview marker is missing: {marker}")
    except (CleanerError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return clean_roots(patterns, options.mode, time.time() - 30 * 24 * 60 * 60,
                       sys.stdout, sys.stderr)


def obsolete_delete_command(arguments):
    parser = argparse.ArgumentParser(description="删除清理根内一个点名的作废事项目标")
    parser.add_argument("--target", required=True, help="目标的字面绝对路径")
    parser.add_argument("--other-sessions-checked", action="store_true", required=True,
                        help="确认已检查其他会话是否依赖此路径")
    options = parser.parse_args(arguments)
    return delete_obsolete(options.target, options.other_sessions_checked,
                           Path.home() / ".claude" / "settings.json",
                           Path.home() / ".config" / "systemd" / "user",
                           sys.stdout, sys.stderr)


def main(arguments):
    if arguments and arguments[0] == "configured":
        return configured_cleaner(arguments[1:])
    if arguments and arguments[0] == "delete-obsolete":
        try:
            return obsolete_delete_command(arguments[1:])
        except (CleanerError, OSError) as error:
            print(error, file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("list", "delete"))
    parser.add_argument("roots", nargs="+", help="root directories or quoted globs")
    options = parser.parse_args(arguments)
    return clean_roots(options.roots, options.mode,
                       time.time() - 30 * 24 * 60 * 60, sys.stdout, sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
