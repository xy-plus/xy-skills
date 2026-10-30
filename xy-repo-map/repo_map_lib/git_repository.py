"""Git root checks, tracked paths, and direct nested repository discovery."""
from pathlib import Path, PurePosixPath
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


def discover_subrepositories(repository):
    """Return child gitlinks and direct nested repositories, excluding this repo's worktrees."""
    root = Path(repository).resolve()
    own_common = _common_directory(root)
    candidates = set(gitlink_paths(root))
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard", "--directory", "-z",
                        check=True)
    ignored = run_git(root, "ls-files", "--others", "--ignored", "--exclude-standard",
                      "--directory", "-z", check=True)
    for result in (untracked, ignored):
        for entry in result.stdout.split("\0"):
            if not entry:
                continue
            normalized = entry.rstrip("/")
            # --directory reports ignored roots such as node_modules once; inspect only that root.
            if len(PurePosixPath(normalized).parts) != 1:
                continue
            candidate = root / normalized
            if _is_repository(candidate):
                common = _common_directory(candidate)
                if common != own_common:
                    candidates.add(normalized)
    return sorted(candidates)


def model_paths(repo_map):
    return sorted(path for path in Path(repo_map).rglob("*.c4")
                  if path.name != "kinds.c4")
