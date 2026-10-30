"""Compare the current map to the first attached worktree's committed map."""
from pathlib import Path
import tempfile

from .approval import approval_changes, approval_digest_line, format_approval_changes
from .git_repository import require_repository_root, run_git
from .likec4 import export_model
from .structure import structure_digest


def _main_branch(repository):
    listing = run_git(repository, "worktree", "list", "--porcelain", check=True).stdout
    first = listing.strip().split("\n\n", 1)[0]
    branch = next((line.removeprefix("branch refs/heads/")
                   for line in first.splitlines() if line.startswith("branch refs/heads/")), None)
    if branch is None:
        raise ValueError("主检出处于分离头指针，无法比较仓图")
    return branch


def _snapshot_map(repository, branch, destination):
    listing = run_git(repository, "ls-tree", "-r", "--name-only", branch, "repo-map",
                       check=True).stdout
    paths = [line for line in listing.splitlines() if line]
    if not paths:
        return None
    map_root = Path(destination) / "repo-map"
    for relative in paths:
        result = run_git(repository, "show", f"{branch}:{relative}", check=True)
        target = Path(destination) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.stdout, encoding="utf-8")
    return map_root


def approval_report(repository):
    """Return human-readable structure changes and the canonical digest line."""
    root = require_repository_root(repository)
    branch = _main_branch(root)
    current = export_model(root / "repo-map")
    with tempfile.TemporaryDirectory(prefix="xy-repo-map-baseline-") as temporary:
        snapshot = _snapshot_map(root, branch, temporary)
        previous = export_model(snapshot) if snapshot is not None else None
    changes = approval_changes(previous, current)
    digest_line = approval_digest_line(structure_digest(current))
    return format_approval_changes(changes), digest_line
