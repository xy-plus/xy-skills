"""Git root checks, tracked paths, and nested repository discovery."""
from pathlib import Path
import os
import subprocess


class ArgumentError(ValueError):
    """The command received a path that cannot be used as a repository root."""


def run_git(repository, *arguments, check):
    result = subprocess.run(["git", "-C", str(repository), *arguments],
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or "git 命令失败")
    return result


def require_repository_root(path):
    """Require an explicit absolute path that is exactly one Git worktree root."""
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ArgumentError("--repo 必须是绝对路径")
    if not candidate.is_dir():
        raise ArgumentError(f"仓目录不存在：{candidate}")
    candidate = candidate.resolve()
    result = run_git(candidate, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise ArgumentError(f"不是 git 仓：{candidate}")
    top = Path(result.stdout.strip()).resolve()
    if top != candidate:
        raise ArgumentError(f"--repo 必须是仓根：{candidate}；仓根为 {top}")
    return candidate


def tracked_paths(repository):
    result = run_git(repository, "ls-files", "-z", check=True)
    return [path for path in result.stdout.split("\0") if path]


def gitlink_paths(repository):
    result = run_git(repository, "ls-files", "--stage", "-z", check=True)
    paths = set()
    for item in result.stdout.split("\0"):
        if not item:
            continue
        metadata, path = item.split("\t", 1)
        if metadata.split(" ", 1)[0] == "160000":
            paths.add(path)
    return paths


def _common_directory(repository):
    result = run_git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir", check=True)
    return Path(result.stdout.strip()).resolve()


def _is_repository(path):
    marker = path / ".git"
    if not marker.exists():
        return False
    result = run_git(path, "rev-parse", "--show-toplevel", check=False)
    return result.returncode == 0


def _is_ignored(repository, relative_path):
    directory_path = relative_path.rstrip("/") + "/"
    result = run_git(repository, "check-ignore", "--quiet", "--", directory_path,
                     check=False)
    return result.returncode == 0


def _untracked_repositories(root, start, own_common):
    found = set()
    pending = [start]
    while pending:
        candidate = pending.pop()
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        relative = candidate.relative_to(root).as_posix()
        if _is_ignored(root, relative):
            continue
        if _is_repository(candidate):
            if _common_directory(candidate) != own_common:
                found.add(relative)
            continue
        try:
            with os.scandir(candidate) as entries:
                pending.extend(Path(entry.path) for entry in entries
                               if entry.name != ".git" and entry.is_dir(follow_symlinks=False))
        except OSError as error:
            raise ValueError(f"无法读取未跟踪目录以发现子仓：{candidate}：{error}") from error
    return found


def discover_subrepositories(repository):
    """Return child gitlinks and direct nested repositories, excluding this repo's worktrees."""
    root = Path(repository).resolve()
    own_common = _common_directory(root)
    candidates = set(gitlink_paths(root))
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard", "--directory", "-z",
                        check=True)
    ignored = run_git(root, "ls-files", "--others", "--ignored", "--exclude-standard",
                      "--directory", "-z", check=True)
    for entry in untracked.stdout.split("\0"):
        if entry:
            candidates.update(_untracked_repositories(root, root / entry.rstrip("/"), own_common))
    for entry in ignored.stdout.split("\0"):
        if not entry:
            continue
        normalized = entry.rstrip("/")
        # Inspect each ignored candidate itself, at any depth, without walking inside it.
        candidate = root / normalized
        if _is_repository(candidate) and _common_directory(candidate) != own_common:
            candidates.add(normalized)
    return sorted(candidates)


def model_paths(repo_map):
    root = Path(repo_map)
    kinds_path = root / "kinds.c4"
    return sorted(path for path in root.rglob("*.c4") if path != kinds_path)
