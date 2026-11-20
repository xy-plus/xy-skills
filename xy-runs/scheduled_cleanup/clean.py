"""Remove whole expired, unused items from explicitly supplied Linux roots."""

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import fcntl
import fnmatch
import glob
import json
import os
from pathlib import Path
import pwd
import re
import shlex
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


def process_is_owned_by_user(process, user):
    try:
        status = (process / "status").read_bytes()
        owner = next(line.split()[1] for line in status.splitlines()
                     if line.startswith(b"Uid:"))
        return int(owner) == user
    except (OSError, StopIteration, ValueError):
        return False


def process_references_path(process, path, command_path):
    try:
        command = os.fsdecode((process / "cmdline").read_bytes())
        if command_path.search(command):
            return True
    except OSError:
        pass
    for reference in process_paths(process):
        if reference_matches_path(reference, path):
            return True
    return False


def reference_matches_path(reference, path):
    if reference == path or reference.startswith(path + "/"):
        return True
    reference = reference.removesuffix(" (deleted)")
    return reference == path or reference.startswith(path + "/")


def is_in_use(item):
    path = str(item)
    command_path = re.compile(r"(?<![\w/.\-])" + re.escape(path) + r"(?=[/\s\x00]|$)")
    user = os.getuid()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        # /proc/<pid> ownership changes for non-dumpable processes. The real
        # UID in status remains readable, so do not use stat.st_uid.
        if process_is_owned_by_user(process, user) and process_references_path(
                process, path, command_path):
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
    other_sessions_checked: bool
    settings_path: Path
    units_directory: Path
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
    if is_in_use(candidate.root / candidate.name):
        if candidate.required:
            raise CleanerError("点名目标仍被本用户进程使用")
        return None
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
        if not is_in_use(item):
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
    item_root = validate_obsolete_location(root, target)
    target_info, resolved_target = validate_obsolete_resolution(root, target)
    return root, target, item_root, root_info, target_info, resolved_target


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
    return target_info, resolved_target


SYSTEMD_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SYSTEMD_UNIT_SUFFIXES = (
    ".service", ".socket", ".target", ".timer", ".path", ".mount",
    ".automount", ".swap", ".slice", ".scope", ".device", ".busname",
)


def systemd_error(unit, source, reason):
    return CleanerError(f"systemd 单元 {unit} 无法完整检查（{source}）：{reason}")


def systemd_user_specifiers():
    """Match systemd.unit(5) user-manager paths using the manager environment."""
    home = Path.home()

    def xdg_directory(name, fallback):
        configured = os.environ.get(name)
        if configured and os.path.isabs(configured):
            return configured
        return str(home / fallback)

    temporary = next((os.environ[name] for name in ("TMPDIR", "TEMP", "TMP")
                      if os.environ.get(name)), None)
    state = xdg_directory("XDG_STATE_HOME", ".local/state")
    specifiers = {
        "h": str(home),
        "S": state,
        "C": xdg_directory("XDG_CACHE_HOME", ".cache"),
        "L": str(Path(state) / "log"),
        "E": xdg_directory("XDG_CONFIG_HOME", ".config"),
        "T": temporary or "/tmp",
        "V": temporary or "/var/tmp",
        "U": str(os.getuid()),
    }
    try:
        specifiers["u"] = pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError):
        pass
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        specifiers["t"] = runtime
    return specifiers


def expand_systemd_specifiers(value, unit, source):
    specifiers = systemd_user_specifiers()
    expanded = []
    position = 0
    while position < len(value):
        if value[position] != "%":
            expanded.append(value[position])
            position += 1
            continue
        if position + 1 == len(value):
            expanded.append("%")
            position += 1
            continue
        specifier = value[position + 1]
        if specifier == "%":
            expanded.append("%%")
        elif specifier in specifiers:
            expanded.append(specifiers[specifier])
        else:
            # Check unknown specifiers after tokenization: %i is harmless as a
            # standalone argument, but unresolved inside a path must block deletion.
            expanded.append("%" + specifier)
        position += 2
    return "".join(expanded)


def split_systemd_words(value, unit, source):
    try:
        return shlex.split(value, posix=True)
    except ValueError as error:
        raise systemd_error(unit, source, f"引号不匹配或转义无效：{error}") from error


def parse_systemd_text(content, unit, source):
    lines = []
    for number, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("["):
            if stripped.endswith("]"):
                continue
            raise systemd_error(unit, source, f"第 {number} 行段标题格式错误")
        directive, separator, value = line.partition("=")
        directive = directive.strip()
        if not separator or not directive:
            raise systemd_error(unit, source, f"第 {number} 行无法解析")
        expanded = expand_systemd_specifiers(value, unit, source)
        split_systemd_words(expanded, unit, source)
        lines.append((directive, expanded, source))
    return lines


def parse_systemd_assignments(value, unit, source):
    assignments = []
    for word in split_systemd_words(value, unit, source):
        name, separator, content = word.partition("=")
        if not separator or SYSTEMD_ENVIRONMENT_NAME.fullmatch(name) is None:
            raise systemd_error(unit, source, f"环境变量赋值格式错误：{word}")
        assignments.append((name, content))
    return assignments


def parse_environment_file(content, unit, source):
    assignments = []
    for number, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        expanded = expand_systemd_specifiers(line, unit, source)
        parsed = parse_systemd_assignments(expanded, unit, f"{source}:{number}")
        if len(parsed) != 1:
            raise systemd_error(unit, f"{source}:{number}", "环境文件每行必须只有一项赋值")
        assignments.extend(parsed)
    return assignments


def expand_systemd_variables(value, variables, unit, source, expanding):
    result = []
    position = 0
    while position < len(value):
        if value[position] != "$":
            result.append(value[position])
            position += 1
            continue
        if position + 1 < len(value) and value[position + 1] == "$":
            result.append("$$")
            position += 2
            continue
        if position + 1 < len(value) and value[position + 1] == "{":
            end = value.find("}", position + 2)
            if end < 0:
                result.append(value[position:])
                break
            name = value[position + 2:end]
            next_position = end + 1
        else:
            match = SYSTEMD_ENVIRONMENT_NAME.match(value, position + 1)
            if match is None:
                result.append("$")
                position += 1
                continue
            name = match.group()
            next_position = match.end()
        if SYSTEMD_ENVIRONMENT_NAME.fullmatch(name) is None:
            result.append(value[position:next_position])
            position = next_position
            continue
        if name not in variables:
            result.append(value[position:next_position])
        elif name in expanding:
            result.append(value[position:next_position])
        else:
            result.append(expand_systemd_variables(
                variables[name], variables, unit, source, (*expanding, name)))
        position = next_position
    return "".join(result)


def unresolved_systemd_specifier_at(word, position):
    if position + 1 >= len(word):
        return None, position + 1
    specifier = word[position + 1]
    if specifier == "%":
        return None, position + 2
    if specifier == "f" or specifier.isalnum() or specifier == "{":
        return "%" + specifier, position + 2
    return None, position + 2


def unresolved_systemd_variable_at(word, position):
    if position + 1 >= len(word):
        return "$", position + 1
    if word[position + 1] == "$":
        return None, position + 2
    if word[position + 1] == "{":
        end = word.find("}", position + 2)
        if end < 0:
            return word[position:], len(word)
        return word[position:end + 1], end + 1
    match = SYSTEMD_ENVIRONMENT_NAME.match(word, position + 1)
    if match is None:
        return "$", position + 1
    return "$" + match.group(), match.end()


def unresolved_systemd_reference(word):
    position = 0
    while position < len(word):
        if word[position] == "%":
            unresolved, position = unresolved_systemd_specifier_at(word, position)
        elif word[position] == "$":
            unresolved, position = unresolved_systemd_variable_at(word, position)
        else:
            position += 1
            continue
        if unresolved is not None:
            return unresolved
    return None


def has_systemd_path_specifier(word):
    position = 0
    while position + 1 < len(word):
        if word[position] != "%":
            position += 1
        elif word[position + 1] == "%":
            position += 2
        elif word[position + 1] == "f":
            return True
        else:
            position += 2
    return False


def validate_systemd_path_words(words, unit, source):
    for word in words:
        unresolved = unresolved_systemd_reference(word)
        if unresolved is None:
            continue
        if "/" in word or has_systemd_path_specifier(word):
            raise systemd_error(
                unit, source,
                f"像路径的词 {word!r} 含未解析的占位符或变量 {unresolved}")


def restore_systemd_escapes(value):
    return value.replace("%%", "%").replace("$$", "$")


def collect_systemd_units(directory):
    units = []

    def visit(current):
        try:
            entries = sorted(current.iterdir(), key=lambda path: path.name)
        except OSError as error:
            raise CleanerError(
                f"无法读取 systemd 用户单元目录 {current}：{error}") from error
        for entry in entries:
            if entry.is_symlink():
                # Read unit-file links through to their targets; never traverse linked dirs.
                if any(entry.name.endswith(suffix) for suffix in SYSTEMD_UNIT_SUFFIXES):
                    units.append(entry)
                continue
            try:
                details = entry.lstat()
            except OSError as error:
                raise CleanerError(
                    f"无法检查 systemd 用户单元路径 {entry}：{error}") from error
            if stat.S_ISDIR(details.st_mode):
                if not entry.name.endswith(".d"):
                    visit(entry)
            elif any(entry.name.endswith(suffix) for suffix in SYSTEMD_UNIT_SUFFIXES):
                units.append(entry)

    visit(directory)
    return sorted(units, key=lambda path: str(path))


def systemd_unit_sources(directory, unit):
    unit_path = Path(unit)
    sources = []
    unit_name = unit_path.name
    unit_suffix = next((suffix for suffix in SYSTEMD_UNIT_SUFFIXES
                        if unit_name.endswith(suffix)), None)
    dropin_directories = [directory / f"{unit_name}.d"]
    if unit_suffix is not None:
        dropin_directories.insert(0, directory / f"{unit_suffix[1:]}.d")
    try:
        if os.path.lexists(unit_path):
            sources.append(unit_path)
        for dropin_directory in dropin_directories:
            if os.path.lexists(dropin_directory):
                details = dropin_directory.lstat()
                if not stat.S_ISDIR(details.st_mode):
                    raise systemd_error(
                        unit_path, dropin_directory, "drop-in 路径不是实际目录")
                sources.extend(sorted((path for path in dropin_directory.iterdir()
                                       if path.name.endswith(".conf")),
                                      key=lambda path: path.name))
    except OSError as error:
        raise systemd_error(unit_path, directory, f"无法读取单元来源：{error}") from error
    return unit_path, sources


def read_systemd_unit_lines(unit, sources):
    lines = []
    for source in sources:
        try:
            content = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise systemd_error(unit, source, f"无法读取单元文本：{error}") from error
        if "\x00" in content:
            raise systemd_error(unit, source, "单元文本包含 NUL 字节")
        lines.extend(parse_systemd_text(content, unit, source))
    return lines


def collect_systemd_environment(lines, unit):
    manager_environment_names = (
        "HOME", "USER", "LOGNAME", "SHELL", "XDG_RUNTIME_DIR",
        "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
        "TMPDIR", "TEMP", "TMP",
    )
    manager_environment = {name: os.environ[name] for name in manager_environment_names
                           if name in os.environ}
    variables = dict(manager_environment)
    environment_files = []
    for directive, value, source in lines:
        if directive == "Environment":
            if not value.strip():
                variables = dict(manager_environment)
                continue
            variables.update(parse_systemd_assignments(value, unit, source))
        elif directive == "EnvironmentFile":
            words = split_systemd_words(value, unit, source)
            if not words:
                environment_files.clear()
                continue
            environment_files.extend((word, source) for word in words)
    return variables, environment_files


def load_systemd_environment_files(unit, environment_files, variables):
    lines = []
    for environment_file, source in environment_files:
        optional = environment_file.startswith("-")
        file_value = environment_file[1:] if optional else environment_file
        file_value = expand_systemd_variables(file_value, variables, unit, source, ())
        environment_words = split_systemd_words(file_value, unit, source)
        validate_systemd_path_words(environment_words, unit, source)
        file_value = restore_systemd_escapes(file_value)
        if not os.path.isabs(file_value):
            raise systemd_error(unit, source, f"EnvironmentFile 路径不是绝对路径：{file_value}")
        matches = sorted(glob.glob(file_value, recursive=True))
        if not matches and not optional:
            raise systemd_error(unit, file_value, "EnvironmentFile 不存在或没有匹配文件")
        for match in matches:
            path = Path(match)
            try:
                details = path.stat()
                if not stat.S_ISREG(details.st_mode):
                    raise OSError("不是普通文件")
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise systemd_error(unit, path, f"无法读取 EnvironmentFile：{error}") from error
            assignments = parse_environment_file(content, unit, path)
            for name, value in assignments:
                variables[name] = value
                lines.append(("Environment", f"{name}={value}", path))
    return lines


def resolve_systemd_unit(unit, sources):
    lines = read_systemd_unit_lines(unit, sources)
    variables, environment_files = collect_systemd_environment(lines, unit)
    lines.extend(load_systemd_environment_files(unit, environment_files, variables))
    expanded_lines = []
    for directive, value, source in lines:
        expanded = expand_systemd_variables(value, variables, unit, source, ())
        words = split_systemd_words(expanded, unit, source)
        validate_systemd_path_words(words, unit, source)
        restored = restore_systemd_escapes(expanded)
        restored_words = split_systemd_words(restored, unit, source)
        expanded_lines.append((directive, restored, restored_words))
    return expanded_lines


def paths_and_ancestors(paths, runs_root):
    root = Path(runs_root)
    candidates = set()
    for path in paths:
        current = Path(path)
        if not current.is_relative_to(root):
            continue
        while current.is_relative_to(root):
            candidates.add(str(current))
            if current == root:
                break
            current = current.parent
    return candidates


def path_matches_systemd_pattern(path, pattern):
    path_parts = Path(path).parts
    pattern_parts = Path(pattern).parts
    positions = {0}
    for pattern_part in pattern_parts:
        if pattern_part == "**":
            expanded = set(positions)
            for position in positions:
                while position < len(path_parts):
                    position += 1
                    expanded.add(position)
            positions = expanded
        else:
            positions = {
                position + 1 for position in positions
                if position < len(path_parts)
                and fnmatch.fnmatchcase(path_parts[position], pattern_part)
            }
    return len(path_parts) in positions


def contains_systemd_path(text, path):
    left_boundaries = "\"'=,:;([ \t\r\n"
    right_boundaries = "\"'=,:;)]} \t\r\n"
    start = 0
    while True:
        start = text.find(path, start)
        if start < 0:
            return False
        end = start + len(path)
        before = text[start - 1] if start else None
        after = text[end] if end < len(text) else None
        left_ok = before is None or before in left_boundaries
        right_ok = after is None or after == "/" or after in right_boundaries
        if left_ok and right_ok:
            return True
        start += 1


def systemd_path_patterns(lines):
    patterns = set()
    for _, _, words in lines:
        for word in words:
            candidates = [word]
            if word.startswith("-"):
                candidates.append(word[1:])
            if "=" in word:
                candidates.append(word.split("=", 1)[1])
            for candidate in candidates:
                candidate = candidate.rstrip(",;")
                if candidate.startswith("/") and glob.has_magic(candidate):
                    patterns.add(candidate)
    return patterns


def systemd_lines_reference_target(lines, paths, runs_root):
    candidates = paths_and_ancestors(paths, runs_root)
    for _, value, _ in lines:
        if any(contains_systemd_path(value, str(path)) for path in paths):
            return True
    return any(path_matches_systemd_pattern(path, pattern)
               for pattern in systemd_path_patterns(lines) for path in candidates)


def find_systemd_reference(directory, paths, runs_root):
    if not os.path.lexists(directory):
        return None
    try:
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise CleanerError(f"systemd 用户单元目录不是实际目录：{directory}")
        for unit_path in collect_systemd_units(directory):
            unit_path, sources = systemd_unit_sources(directory, unit_path)
            if not sources:
                continue
            lines = resolve_systemd_unit(unit_path, sources)
            if systemd_lines_reference_target(lines, paths, runs_root):
                return unit_path
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


def delete_obsolete(request):
    if not request.other_sessions_checked:
        raise CleanerError("删除前必须显式确认已检查其他会话依赖")
    root, target, item_root, root_info, target_info, resolved_target = validate_obsolete_target(
        read_runs_root(request.settings_path), request.target_value)
    if is_in_use(target) or (resolved_target != target and is_in_use(resolved_target)):
        raise CleanerError(f"目标仍被本用户进程使用：{target}")
    unit = find_systemd_reference(request.units_directory, (target, resolved_target), root)
    if unit is not None:
        raise CleanerError(f"systemd 用户单元引用目标：{unit}")
    task = find_runner_reference(item_root, target, resolved_target)
    if task is not None:
        raise CleanerError(f"sub-agent-runner 任务可用 --dir 续跑目标：{task}")

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
    parser.add_argument("--other-sessions-checked", action="store_true", required=True,
                        help="确认已检查其他会话是否依赖此路径")
    options = parser.parse_args(arguments)
    request = ObsoleteDeleteRequest(
        options.target, options.other_sessions_checked,
        Path.home() / ".claude" / "settings.json",
        Path.home() / ".config" / "systemd" / "user",
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
