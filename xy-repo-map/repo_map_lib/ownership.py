"""Map each tracked repository path to exactly one model element."""
import posixpath
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse


class Owner(NamedTuple):
    element: str
    category: str


def link_records(model):
    """Return normalized links as (element id, repo-relative path, category)."""
    records = []
    for element_id, element in model.get("elements", {}).items():
        for link in element.get("links", []):
            url = link.get("url", "")
            parsed = urlparse(url)
            if parsed.scheme or parsed.netloc or url.startswith("/"):
                records.append((element_id, None, link.get("title"), url))
                continue
            normalized = posixpath.normpath(posixpath.join("repo-map", parsed.path))
            if normalized.startswith("repo-map/"):
                relative = normalized[len("repo-map/"):]
            elif normalized == "repo-map":
                relative = "."
            elif normalized == ".." or normalized.startswith("../"):
                relative = None
            else:
                relative = normalized
            records.append((element_id, relative, link.get("title"), url))
    return records


def check_ownership(repo_root, model, tracked_paths, gitlink_paths, subrepo_paths):
    """Check link targets, path overlap, gitlinks, child repositories, and coverage."""
    root = Path(repo_root)
    problems = []
    records = link_records(model)
    targets = {}
    for element_id, target, category, original in records:
        if target is None:
            problems.append(f"链接必须是 repo-map 内的相对路径：{element_id} -> {original}")
            continue
        if target in targets:
            problems.append(f"重复链接路径 {target}：{targets[target]} 与 {element_id}")
        else:
            targets[target] = element_id
        if target != "." and not (root / target).exists():
            problems.append(f"链接目标不存在：{element_id} -> {target}")

    child_links = {}
    for element_id, target, category, original in records:
        if target is None or category != "repo":
            continue
        child_links.setdefault(target, []).append(element_id)
        if target not in subrepo_paths:
            problems.append(f"repo 链接没有指向识别出的子仓：{element_id} -> {target}")
        elif not (root / target / "repo-map").is_dir():
            problems.append(f"子仓缺少 repo-map：{target}")
    for subrepo in sorted(set(subrepo_paths)):
        matching = child_links.get(subrepo, [])
        if len(matching) != 1:
            problems.append(f"子仓 {subrepo} 必须对应恰好一个 repo 元素，实际 {len(matching)} 个")

    owners = {}
    for tracked in sorted(set(tracked_paths)):
        if tracked == "repo-map" or tracked.startswith("repo-map/"):
            continue
        if tracked in gitlink_paths:
            matching = [(element_id, target, category) for element_id, target, category, _ in records
                        if target == tracked]
            if len(matching) != 1 or matching[0][2] != "repo":
                problems.append(f"submodule gitlink {tracked} 只能由 repo 链接覆盖")
                continue
        candidates = []
        for element_id, target, category, _ in records:
            if target is None:
                continue
            target_path = root / target
            is_directory = target == "." or target_path.is_dir()
            below_target = target == "." or tracked.startswith(target.rstrip("/") + "/")
            if tracked == target or (is_directory and below_target):
                candidates.append((len(target), element_id, category))
        if not candidates:
            problems.append(f"跟踪文件未归属任何节点：{tracked}")
            continue
        longest = max(size for size, _, _ in candidates)
        winners = [(element_id, category) for size, element_id, category in candidates if size == longest]
        if len(winners) != 1:
            problems.append(f"跟踪文件有多个最长路径归属：{tracked}")
            continue
        owners[tracked] = Owner(*winners[0])
        if tracked in gitlink_paths and winners[0][1] != "repo":
            problems.append(f"submodule gitlink {tracked} 只能由 repo 链接覆盖")
    return problems, owners
