#!/usr/bin/env python3
"""检查当前仓库和子仓库中超过 500 行的 Git 跟踪文本文件。"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

MAX_LINES = 500
EXCEPTIONS_DIRECTORY = "file-length-exceptions"
SKIP_DIRECTORIES = {".git", "target", "build", "dist", "reports"}
EXCEPTION_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]")


def exception_name(relative_path: str) -> str:
    return EXCEPTION_NAME_PATTERN.sub("-", relative_path.rstrip("/"))


def run_git(repository: Path, *arguments: str) -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(repository), *arguments], stderr=subprocess.PIPE
    )


def find_repositories(root: Path, problems: list[str]) -> list[Path]:
    """遍历 root 找所有含 .git 的目录；同一仓的多个工作树按 common-dir 去重。

    只跳过 `.git` 内部与构建/测试产物 `target`、`build`、`dist`、`reports`：
    `reports/` 是 workspace 的测试产物根，pytest 临时目录里会有复制出来的假 `.git`。
    """
    candidates: list[Path] = []

    def traversal_error(error: OSError) -> None:
        raise error

    for current_text, directories, files in os.walk(
        root, topdown=True, onerror=traversal_error
    ):
        current = Path(current_text)
        directories[:] = sorted(
            name for name in directories if name not in SKIP_DIRECTORIES
        )
        if os.path.lexists(current / ".git"):
            candidates.append(current)

    repositories: list[Path] = []
    seen_common_directories: set[Path] = set()
    for candidate in candidates:
        try:
            common_text = os.fsdecode(run_git(candidate, "rev-parse", "--git-common-dir")).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            problems.append(f"{candidate}：读 git-common-dir 失败：{error}")
            continue
        common = Path(common_text)
        if not common.is_absolute():
            common = candidate / common
        common = common.resolve()
        if common in seen_common_directories:
            continue
        seen_common_directories.add(common)
        repositories.append(candidate)
    return repositories


def tracked_paths(repository: Path, problems: list[str]) -> list[str] | None:
    try:
        output = run_git(repository, "ls-files", "-z")
    except (OSError, subprocess.CalledProcessError) as error:
        problems.append(f"{repository}：git ls-files 失败：{error}")
        return None
    return [os.fsdecode(path) for path in output.split(b"\0") if path]


def path_kinds(paths: list[str]) -> dict[str, list[tuple[str, str]]]:
    names: dict[str, list[tuple[str, str]]] = {}
    for relative in paths:
        names.setdefault(exception_name(relative), []).append(("文件", relative))
        parent = posixpath.dirname(relative)
        while parent:
            names.setdefault(exception_name(parent), []).append(("目录", parent))
            parent = posixpath.dirname(parent)
    return names


def read_text_file(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def check_repository(repository: Path, root: Path, problems: list[str]) -> None:
    where = repository.relative_to(root).as_posix() or "."
    paths = tracked_paths(repository, problems)
    if paths is None:
        return
    tracked = set(paths)
    candidates = path_kinds(paths)
    for name, matches in candidates.items():
        unique_matches = set(matches)
        if len(unique_matches) > 1:
            problems.append(f"{where}：多个路径映射为同一条目名：{name}")

    line_counts: dict[str, int] = {}
    for relative in paths:
        path = repository / relative
        try:
            if path.is_symlink() or not path.is_file():
                continue
        except OSError as error:
            problems.append(f"{where}：读取文件属性失败：{relative}：{error}")
            continue
        try:
            text = read_text_file(path)
        except OSError as error:
            problems.append(f"{where}：读取文件失败：{relative}：{error}")
            continue
        if text is not None:
            line_counts[relative] = len(text.splitlines())
    over_limit = {
        relative for relative, count in line_counts.items() if count > MAX_LINES
    }

    exception_directory = repository / EXCEPTIONS_DIRECTORY
    file_entries: dict[str, str] = {}
    directory_entries: dict[str, str] = {}
    if exception_directory.exists():
        if not exception_directory.is_dir():
            problems.append(f"{where}：{EXCEPTIONS_DIRECTORY} 不是目录")
        else:
            try:
                entries = sorted(exception_directory.iterdir(), key=lambda item: item.name)
            except OSError as error:
                problems.append(f"{where}：读取理由目录失败：{error}")
                entries = []
            for entry in entries:
                if entry.is_dir():
                    problems.append(f"{where}：理由目录里不许有子目录：{entry.name}")
                    continue
                if entry.is_symlink():
                    problems.append(f"{where}：理由条目不许是符号链接：{entry.name}")
                    continue
                if not entry.is_file():
                    problems.append(f"{where}：理由条目不是普通文件：{entry.name}")
                    continue
                entry_path = f"{EXCEPTIONS_DIRECTORY}/{entry.name}"
                if entry_path not in tracked:
                    problems.append(f"{where}：条目没有计入 Git：{entry.name}")
                    continue
                try:
                    reason = entry.read_text(encoding="utf-8").strip()
                except (OSError, UnicodeDecodeError) as error:
                    problems.append(f"{where}：理由文件无法读取：{entry.name}：{error}")
                    continue
                if not reason:
                    problems.append(f"{where}：理由为空：{entry.name}")
                    continue
                if entry.name == "-":
                    problems.append(f"{where}：不许根目录条目：{entry.name}")
                    continue
                matches = candidates.get(entry.name, [])
                unique_matches = set(matches)
                if len(unique_matches) > 1:
                    problems.append(f"{where}：条目名有歧义：{entry.name}")
                    continue
                if not matches:
                    problems.append(f"{where}：条目不对应 Git 跟踪的文件或目录：{entry.name}")
                    continue
                kind, relative = matches[0]
                if kind == "文件":
                    file_entries[relative] = reason
                else:
                    directory_entries[relative] = reason
    elif over_limit:
        problems.append(f"{where}：有超限文件，但没有 {EXCEPTIONS_DIRECTORY}/ 目录")

    for relative in file_entries:
        if relative not in over_limit:
            problems.append(f"{where}：文件条目已过期：{relative}")
    for relative in directory_entries:
        directory_path = repository / relative
        if not directory_path.is_dir() or not any(
            item.startswith(relative + "/") for item in over_limit
        ):
            problems.append(f"{where}：目录条目已过期：{relative}")

    for relative in sorted(line_counts):
        count = line_counts[relative]
        if count < MAX_LINES:
            continue
        if count == MAX_LINES:
            print(f"{where}: {relative}: {count} 行，到上限")
            continue
        reason = file_entries.get(relative)
        parent = posixpath.dirname(relative)
        while reason is None and parent:
            reason = directory_entries.get(parent)
            parent = posixpath.dirname(parent)
        if reason is None:
            problems.append(f"{where}: {relative}: {count} 行，没有理由文件")
        else:
            print(f"{where}: {relative}: {count} 行，有理由")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Git 仓库顶层的绝对路径")
    arguments = parser.parse_args()
    root_argument = arguments.root
    if not os.path.isabs(root_argument):
        print("--root 必须是 Git 仓顶层的绝对路径", file=sys.stderr)
        return 2
    root = Path(root_argument)
    try:
        top_level = os.fsdecode(run_git(root, "rev-parse", "--show-toplevel")).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"--root 不是可用的 Git 仓：{error}", file=sys.stderr)
        return 2
    if top_level != root_argument:
        print(f"--root 必须是 Git 仓顶层：{top_level}", file=sys.stderr)
        return 2

    problems: list[str] = []
    try:
        repositories = find_repositories(root, problems)
    except OSError as error:
        print(f"遍历失败：{error}", file=sys.stderr)
        return 1
    print(f"发现仓库：{len(repositories)}")
    for repository in repositories:
        print(f"检查仓库：{repository.relative_to(root).as_posix() or '.'}")
        check_repository(repository, root, problems)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
