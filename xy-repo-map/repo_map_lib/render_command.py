"""Check a repository tree, combine its models, and build a static site."""
from pathlib import Path
import json
import shutil
import tempfile

from .checker import check_repository
from .constants import CODE_SUFFIXES, STANDARD_KINDS
from .git_repository import model_paths, require_repository_root, tracked_paths, gitlink_paths, discover_subrepositories
from .likec4 import build_likec4
from .metrics import ZERO_COUNTS, LineCounts, count_file, coverage_counts
from .ownership import category_for_path, check_ownership, link_records
from .rendering import (add_counts, add_coverage, coverage_text,
                        generate_views)


def model_file_destination(project, root_id, relative_path):
    """Prefix each source filename while preserving folders to avoid flattening collisions."""
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"模型文件路径必须是仓图内的相对路径：{relative_path}")
    return Path(project) / root_id / relative.parent / f"{root_id}-{relative.name}"


def _root_id(model):
    roots = [element_id for element_id in model.get("elements", {}) if "." not in element_id]
    if len(roots) != 1:
        raise ValueError("模型没有唯一根节点")
    return roots[0]


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
        current_problems, model = check_repository(resolved, check_approval)
        if current_problems:
            problems.extend(f"仓 {resolved}：{problem}" for problem in current_problems)
            return
        root_id = _root_id(model)
        item = {"path": resolved, "repo_map": resolved / "repo-map",
                "root_id": root_id, "model": model}
        repositories.append(item)
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


def _combined_model(repositories):
    combined = {"elements": {}, "relations": {}}
    for item in repositories:
        for element_id, element in item["model"].get("elements", {}).items():
            if element_id in combined["elements"]:
                raise ValueError(f"组合模型有重复元素 id：{element_id}")
            combined["elements"][element_id] = element
        for relation_id, relation in item["model"].get("relations", {}).items():
            key = f"{item['root_id']}_{relation_id}"
            if key in combined["relations"]:
                raise ValueError(f"组合模型有重复关系 id：{key}")
            combined["relations"][key] = relation
    return combined


def _collect_statistics(repositories, combined):
    own_counts = {element_id: ZERO_COUNTS for element_id in combined["elements"]}
    own_coverage = {element_id: {} for element_id in combined["elements"]}
    for item in repositories:
        root = item["path"]
        tracked = tracked_paths(root)
        gitlinks = gitlink_paths(root)
        subrepos = discover_subrepositories(root)
        _, owners = check_ownership(root, item["model"], tracked, gitlinks, subrepos)
        for file_path, owner in owners.items():
            category = category_for_path(root, item["model"], file_path)
            if category not in ("code", "docs", "tests"):
                continue
            try:
                content = (root / file_path).read_bytes()
            except OSError as error:
                raise ValueError(f"无法读取归属文件以统计行数：{root / file_path}：{error}") from error
            own_counts[owner] = add_counts(own_counts[owner], count_file(file_path, category, content))
            if category == "code":
                covered, total, language = coverage_counts(file_path, content.decode("utf-8", errors="ignore"))
                if language == "不适用":
                    language = CODE_SUFFIXES[Path(file_path).suffix.lower()]
                    own_coverage[owner] = add_coverage(own_coverage[owner], {"不适用": (0, 0)})
                else:
                    own_coverage[owner] = add_coverage(
                        own_coverage[owner], {language: (covered, total)})

    repo_targets = {}
    repositories_by_path = {item["path"]: item for item in repositories}
    for item in repositories:
        for element_id, target, category, _ in link_records(item["model"]):
            if category == "repo" and target is not None:
                child_path = (item["path"] / target).resolve()
                child = repositories_by_path.get(child_path)
                if child is not None:
                    repo_targets[element_id] = child["root_id"]

    children = {}
    for element_id in combined["elements"]:
        parent = element_id.rpartition(".")[0]
        if parent:
            children.setdefault(parent, []).append(element_id)
    totals = {}
    coverages = {}
    active = set()

    def calculate(element_id):
        if element_id in totals:
            return totals[element_id], coverages[element_id]
        if element_id in active:
            raise ValueError(f"组合模型出现循环层级：{element_id}")
        active.add(element_id)
        if element_id in repo_targets:
            result = calculate(repo_targets[element_id])
            active.remove(element_id)
            totals[element_id], coverages[element_id] = result
            return result
        count = own_counts[element_id]
        coverage = own_coverage[element_id]
        for child_id in children.get(element_id, []):
            child_count, child_coverage = calculate(child_id)
            count = add_counts(count, child_count)
            coverage = add_coverage(coverage, child_coverage)
        active.remove(element_id)
        totals[element_id] = count
        coverages[element_id] = coverage
        return count, coverage

    for element_id in combined["elements"]:
        calculate(element_id)
    return totals, {element_id: coverage_text(value) for element_id, value in coverages.items()}, repo_targets


def _assemble_project(repositories, temporary_directory, mode):
    project = Path(temporary_directory)
    project.mkdir(parents=True, exist_ok=True)
    combined = _combined_model(repositories)
    totals, coverage, repo_targets = _collect_statistics(repositories, combined)
    (project / "likec4.config.json").write_text(
        json.dumps({"name": repositories[0]["root_id"]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (project / "kinds.c4").write_text(STANDARD_KINDS, encoding="utf-8")

    # Nested LikeC4 projects duplicate element kind definitions; use one project and one kinds.c4.
    copied_names = set()
    for item in repositories:
        for source in model_paths(item["repo_map"]):
            relative = source.relative_to(item["repo_map"])
            destination = model_file_destination(project, item["root_id"], relative)
            destination_name = destination.relative_to(project).as_posix()
            if destination_name in copied_names:
                raise ValueError(f"组合模型源文件重名：{destination_name}")
            copied_names.add(destination_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    top_roots = [repositories[0]["root_id"]]
    (project / "generated-views.c4").write_text(
        generate_views(combined, top_roots, mode, totals, repo_targets, coverage), encoding="utf-8")
    return project


def render_repository(repository, mode, output):
    """Render a checked repository tree into a new or empty output directory."""
    if mode not in ("草稿", "正式"):
        return ["mode 只能是 草稿 或 正式"]
    destination = Path(output).expanduser()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        return [f"--out 必须是不存在或为空的目录：{destination}"]
    try:
        tree_problems, repositories = _checked_tree(repository, mode)
    except ValueError as error:
        return [str(error)]
    if tree_problems:
        return tree_problems
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return [f"无法创建 --out 父目录：{error}"]
    created = not destination.exists()
    if created:
        destination.mkdir()

    try:
        with tempfile.TemporaryDirectory(prefix="xy-repo-map-project-") as temporary:
            project = _assemble_project(repositories, Path(temporary) / "project", mode)
            result = build_likec4(project, destination)
        if result.returncode != 0:
            details = (result.stdout + result.stderr).strip()
            raise ValueError(f"LikeC4 build 失败：{details}")
        if mode == "草稿":
            (destination / "DRAFT").write_text("草稿·未经同意\n", encoding="utf-8")
        return []
    except (OSError, ValueError) as error:
        if created and destination.exists():
            shutil.rmtree(destination)
        return [str(error)]
