"""Remove whole expired items from explicitly supplied Linux roots."""

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import fcntl
import glob
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time
import uuid
from typing import Optional


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
        # Only widen current-user directories; dirfds keep this walk off symlinks.
        def add_write_permissions(descriptor):
            details = os.fstat(descriptor)
            if details.st_uid == os.getuid():
                os.fchmod(descriptor, stat.S_IMODE(details.st_mode)
                          | stat.S_IWUSR | stat.S_IXUSR)
            with os.scandir(descriptor) as children:
                for child in children:
                    if not child.is_dir(follow_symlinks=False):
                        continue
                    expected = child.stat(follow_symlinks=False)
                    child_descriptor = os.open(
                        child.name, DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
                    try:
                        if not os.path.samestat(expected, os.fstat(child_descriptor)):
                            raise OSError(f"目录在删除前发生变化：{child.name}")
                        add_write_permissions(child_descriptor)
                    finally:
                        os.close(child_descriptor)

        descriptor = os.open(path, DIRECTORY_OPEN_FLAGS)
        try:
            add_write_permissions(descriptor)
        finally:
            os.close(descriptor)
        shutil.rmtree(path)
    else:
        path.unlink()


class CleanerError(ValueError):
    """A configured path or explicitly selected target violates the cleanup boundary."""


@dataclass(frozen=True)
class CleanupPlan:
    roots: dict
    outer_roots: dict
    mode: str
    cutoff: Optional[float]
    output: object
    errors: object


@dataclass(frozen=True)
class DeleteCandidate:
    root: Path
    outer: Path
    name: str
    expected_item: object
    required: bool


@dataclass(frozen=True)
class ObsoleteDeleteRequest:
    target_value: str
    settings_path: Path
    output: object
    errors: object


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


def validate_candidate(plan, candidate, root_descriptor):
    current = os.stat(candidate.name, dir_fd=root_descriptor, follow_symlinks=False)
    if not os.path.samestat(candidate.expected_item, current):
        if candidate.required:
            raise CleanerError("点名目标已被替换")
        return None
    entry = Path(f"/proc/self/fd/{root_descriptor}") / candidate.name
    if plan.cutoff is not None and not is_expired(entry, plan.cutoff):
        if candidate.required:
            raise CleanerError("点名目标的年龄状态已变化")
        return None
    if candidate.required and not entry.resolve(strict=True).is_relative_to(candidate.outer):
        raise CleanerError("软链真实目标不在 RUNS_ROOT 内")
    return entry


def move_candidate_to_trash(root_descriptor, outer_descriptor, handles, name):
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
    return destination


def delete_candidate(plan, candidate):
    item = candidate.root / candidate.name
    try:
        with checked_directory(candidate.outer, plan.roots[candidate.outer]) as outer_descriptor:
            fcntl.flock(outer_descriptor, fcntl.LOCK_EX)
            with ExitStack() as handles:
                with checked_directory(candidate.root, plan.roots[candidate.root]) as root_descriptor:
                    entry = validate_candidate(plan, candidate, root_descriptor)
                    if entry is None:
                        return 0
                    destination = move_candidate_to_trash(
                        root_descriptor, outer_descriptor, handles, candidate.name)
                remove_item(destination)
                print(json.dumps(str(item), ensure_ascii=True), file=plan.output)
    except (OSError, CleanerError) as error:
        print(f"error: {item}: {error}", file=plan.errors)
        return 1
    return 0


def clean_roots(patterns, mode, cutoff, output, errors):
    """Run expiry checks around two locked transactions: trash recovery and one-item deletion."""
    roots = expand_roots(patterns, errors)
    outer_roots = {root: next(outer for outer in roots if root.is_relative_to(outer))
                   for root in roots}
    plan = CleanupPlan(roots, outer_roots, mode, cutoff, output, errors)
    status = recover_all_trash(plan)
    for root in roots:
        status |= clean_root(plan, root)
    return status


def recover_all_trash(plan):
    if plan.mode == "list":
        return 0
    status = 0
    for outer in dict.fromkeys(plan.outer_roots.values()):
        status |= recover_trash(outer, plan.roots[outer], plan.errors)
    return status


def clean_root(plan, root):
    # A previously removed ancestor may have contained this nested root.
    if not os.path.lexists(root):
        return 0
    status = 0
    try:
        with checked_directory(root, plan.roots[root]) as root_descriptor:
            for name in sorted(os.listdir(root_descriptor)):
                status |= clean_root_entry(plan, root, root_descriptor, name)
    except OSError as error:
        print(f"error: {root}: {error}", file=plan.errors)
        return 1
    return status


def clean_root_entry(plan, root, root_descriptor, name):
    if name in (TRASH_NAME, ".runs-cleaner-previewed"):
        return 0
    entry = Path(f"/proc/self/fd/{root_descriptor}") / name
    if not is_expired(entry, plan.cutoff):
        return 0
    item = root / name
    if plan.mode == "list":
        print(json.dumps(str(item), ensure_ascii=True), file=plan.output)
        return 0
    expected_item = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
    candidate = DeleteCandidate(root, plan.outer_roots[root], name, expected_item, False)
    return delete_candidate(plan, candidate)


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
    root, target, root_info = literal_obsolete_paths(root_value, target_value)
    validate_obsolete_location(root, target)
    target_info = validate_obsolete_resolution(root, target)
    return root, target, root_info, target_info


def literal_obsolete_paths(root_value, target_value):
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
    return root, target, root_info


def validate_obsolete_location(root, target):
    relative = target.relative_to(root)
    project = root / relative.parts[0]
    item_root = root / relative.parts[0] / relative.parts[1]
    try:
        if not stat.S_ISDIR(project.lstat().st_mode):
            raise CleanerError(f"项目目录必须是真实目录：{project}")
        if not stat.S_ISDIR(item_root.lstat().st_mode):
            raise CleanerError(f"事项目录必须是真实目录：{item_root}")
        if not os.path.lexists(target):
            raise CleanerError(f"点名目标不存在：{target}")
    except OSError as error:
        raise CleanerError(f"目标路径无法安全验证：{target}: {error}") from error
    return item_root


def validate_obsolete_resolution(root, target):
    try:
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
    return target_info


def delete_obsolete(request):
    root, target, root_info, target_info = validate_obsolete_target(
        read_runs_root(request.settings_path), request.target_value)

    parent_info = target.parent.lstat()
    roots = {root: root_info, target.parent: parent_info}
    outer_roots = {root: root, target.parent: root}
    if recover_trash(root, root_info, request.errors) != 0:
        return 1
    plan = CleanupPlan(roots, outer_roots, "delete", None, request.output, request.errors)
    candidate = DeleteCandidate(target.parent, root, target.name, target_info, True)
    return delete_candidate(plan, candidate)


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
    options = parser.parse_args(arguments)
    request = ObsoleteDeleteRequest(
        options.target, Path.home() / ".claude" / "settings.json",
        sys.stdout, sys.stderr,
    )
    return delete_obsolete(request)


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
