"""Git root checks, tracked paths, and subrepository classification."""
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


def primary_checkout_name(repository):
    """Return the directory name of Git's primary checkout, even from a linked worktree."""
    result = run_git(repository, "worktree", "list", "--porcelain", "-z", check=True)
    for item in result.stdout.split("\0"):
        if item.startswith("worktree "):
            return Path(item[len("worktree "):]).name
    raise ValueError("无法从 Git 工作树列表找到主检出")


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


def _discover_subrepository_types(repository):
    """Separate unsupported subrepositories from ignored repositories named directly.

    Ignored candidates are checked at their listed path without walking inside, since ignored
    directories commonly contain worktrees and caches.
    """
    root = Path(repository).resolve()
    own_common = _common_directory(root)
    unsupported = set(gitlink_paths(root))
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard", "--directory", "-z",
                        check=True)
    ignored = run_git(root, "ls-files", "--others", "--ignored", "--exclude-standard",
                      "--directory", "-z", check=True)
    for entry in untracked.stdout.split("\0"):
        if entry:
            unsupported.update(
                _untracked_repositories(root, root / entry.rstrip("/"), own_common)
            )
    ignored_repositories = set()
    for entry in ignored.stdout.split("\0"):
        if not entry:
            continue
        normalized = entry.rstrip("/")
        # Inspect each ignored candidate itself, at any depth, without walking inside it.
        candidate = root / normalized
        if _is_repository(candidate) and _common_directory(candidate) != own_common:
            ignored_repositories.add(normalized)
    return unsupported, ignored_repositories


def discover_subrepositories(repository):
    """Find all gitlinks and nested repositories, without classifying their errors."""
    unsupported, ignored = _discover_subrepository_types(repository)
    return sorted(unsupported | ignored)


def subrepository_errors(repository, registered_subrepositories):
    """Return check errors for unsupported or incorrectly registered subrepositories."""
    root = Path(repository).resolve()
    unsupported, ignored = _discover_subrepository_types(root)
    registered = set(registered_subrepositories)
    errors = []

    if unsupported:
        errors.append("还不支持子仓：" + ", ".join(sorted(unsupported)))

    missing = ignored - registered
    if missing:
        errors.append(
            "被忽略规则直接点名的子仓未登记：" + ", ".join(sorted(missing))
            + "；另有功能图的独立子仓写进根功能的 subrepositories"
        )

    # linked worktree 和新克隆里没有这些被忽略的目录，所以缺失路径不算错误。
    mismatched = {
        path for path in registered
        if os.path.lexists(root / path) and path not in ignored
    }
    if mismatched:
        errors.append(
            "登记的子仓不是被忽略规则直接点名的嵌套 Git 仓："
            + ", ".join(sorted(mismatched))
        )
    return errors
