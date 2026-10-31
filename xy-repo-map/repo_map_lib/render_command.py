"""Check a repository tree, compose its node data, and write a static site."""
from pathlib import Path
import shutil

from .checker import check_repository
from .git_repository import require_repository_root
from .map_data import build_page_data
from .ownership import link_records
from .site import write_site


def _root_id(model):
    roots = [element_id for element_id in model.get("elements", {}) if "." not in element_id]
    if len(roots) != 1:
        raise ValueError("模型没有唯一根节点")
    return roots[0]


def _contains_path(directory, path):
    directory = Path(directory).resolve()
    path = Path(path).resolve()
    return path == directory or directory in path.parents


def _checked_tree(repository, mode):
    root = require_repository_root(repository)
    problems = []
    repositories = []
    known_paths = set()
    check_approval = mode == "正式"

    def visit(path):
        resolved = Path(path).resolve()
        if resolved in known_paths:
            return
        known_paths.add(resolved)
        current_problems, model, owners, _ = check_repository(resolved, check_approval)
        if current_problems:
            problems.extend(f"仓 {resolved}：{problem}" for problem in current_problems)
            return
        root_id = _root_id(model)
        repositories.append({
            "path": resolved, "root_id": root_id, "model": model, "owners": owners,
        })
        for element_id, target, category, _ in link_records(model):
            if category == "repo" and target is not None:
                visit(resolved / target)

    visit(root)
    roots = {}
    for item in repositories:
        roots.setdefault(item["root_id"], []).append(str(item["path"]))
    for root_id, paths in roots.items():
        if len(paths) > 1:
            problems.append(f"整棵仓树的根 id 重复：{root_id}：{', '.join(sorted(paths))}")
    return problems, repositories


def render_repository(repository, mode, output):
    """Check every repository, then write page and JSON without invoking a site builder."""
    if mode not in ("草稿", "正式"):
        return ["mode 只能是 草稿 或 正式"]
    try:
        root = require_repository_root(repository)
    except ValueError as error:
        return [str(error)]
    destination = Path(output).expanduser().resolve()
    if _contains_path(root, destination):
        return [f"--out 不能位于仓内：{destination}（仓根 {root}）"]
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        return [f"--out 必须是不存在或为空的目录：{destination}"]
    try:
        tree_problems, repositories = _checked_tree(root, mode)
        if tree_problems:
            return tree_problems
        for item in repositories:
            if _contains_path(item["path"], destination):
                return [f"--out 不能位于仓内：{destination}（仓根 {item['path']}）"]
        map_data, page_data = build_page_data(repositories, mode)
    except (OSError, ValueError) as error:
        return [str(error)]

    created = not destination.exists()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.mkdir(exist_ok=True)
        write_site(map_data, page_data, destination)
        return []
    except (OSError, ValueError) as error:
        if destination.exists():
            if created:
                shutil.rmtree(destination)
            else:
                for child in destination.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
        return [str(error)]
