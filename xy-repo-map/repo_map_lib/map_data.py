"""Compose checked repository models into page data and its public JSON contract."""
from pathlib import Path

from .metrics import ZERO_COUNTS, count_file, coverage_counts
from .ownership import link_records
from .rendering import add_counts, add_coverage, coverage_text


COUNT_NAMES = ("code", "docs", "comments", "tests")


def _description(element):
    value = element.get("description", "")
    if isinstance(value, dict):
        return value.get("txt", "")
    return value


def _endpoint(value):
    if isinstance(value, dict):
        return value.get("model", value.get("id", ""))
    return value


def _count_dict(counts):
    return {name: getattr(counts, name) for name in COUNT_NAMES}


def _visible_ancestor(element_id, visible, parents):
    current = element_id
    while current is not None:
        if current in visible:
            return current
        current = parents.get(current)
    return None


def _relation_container(source, target, parents, aliases):
    source = aliases.get(source, source)
    target = aliases.get(target, target)

    def parent_of(element_id):
        parent = parents.get(element_id)
        return aliases.get(parent, parent)

    source_ancestors = []
    current = source
    while current is not None:
        source_ancestors.append(current)
        current = parent_of(current)
    target_ancestors = set()
    current = target
    while current is not None:
        target_ancestors.add(current)
        current = parent_of(current)
    common = next((element_id for element_id in source_ancestors
                   if element_id in target_ancestors), None)
    if common in (source, target):
        return parent_of(common)
    return common


def _drill_views(nodes, children, parents, relations, repo_roots):
    result = {}
    repository_root_ids = set(repo_roots.values())
    for element_id in sorted(nodes):
        child_root = repo_roots.get(element_id, element_id)
        visible = children.get(child_root, []) or [element_id]
        visible_set = set(visible)
        labels_by_pair = {}
        for relation in relations:
            source = _visible_ancestor(relation["source"], visible_set, parents)
            target = _visible_ancestor(relation["target"], visible_set, parents)
            if source is None or target is None or source == target:
                continue
            pair = (source, target)
            labels = labels_by_pair.setdefault(pair, [])
            if relation["title"] and relation["title"] not in labels:
                labels.append(relation["title"])
        path = []
        current = element_id
        while current is not None:
            if current not in repository_root_ids:
                path.append(current)
            current = parents.get(current)
        result[element_id] = {
            "nodes": visible,
            "relations": [
                {"source": source, "target": target, "title": " · ".join(labels)}
                for (source, target), labels in sorted(labels_by_pair.items())
            ],
            "breadcrumbs": list(reversed(path)),
        }
    return result


def build_page_data(repositories, mode):
    """Build page data from models and owners already checked for each repository."""
    if mode not in ("草稿", "正式"):
        raise ValueError("mode 只能是 草稿 或 正式")

    elements = {}
    repository_by_path = {Path(item["path"]).resolve(): item for item in repositories}
    root_ids = set()
    for item in repositories:
        root_id = item["root_id"]
        if root_id in root_ids:
            raise ValueError(f"整棵仓树的根 id 重复：{root_id}")
        root_ids.add(root_id)
        for element_id, element in item["model"].get("elements", {}).items():
            if element_id in elements:
                raise ValueError(f"组合模型有重复元素 id：{element_id}")
            elements[element_id] = element

    repo_roots = {}
    parent_for_root = {}
    relations = []
    for item in repositories:
        model = item["model"]
        for element_id, target, category, _ in link_records(model):
            if category != "repo" or target is None:
                continue
            child_path = (Path(item["path"]) / target).resolve()
            child = repository_by_path.get(child_path)
            if child is None:
                continue
            child_root = child["root_id"]
            if element_id in repo_roots or child_root in parent_for_root:
                raise ValueError(f"子仓入口重复：{element_id} → {child_root}")
            repo_roots[element_id] = child_root
            parent_for_root[child_root] = element_id
        for relation in model.get("relations", {}).values():
            relations.append({
                "source": _endpoint(relation.get("source", "")),
                "target": _endpoint(relation.get("target", "")),
                "title": relation.get("title", ""),
            })

    parents = {}
    children = {}
    for element_id in elements:
        parent = parent_for_root.get(element_id, element_id.rpartition(".")[0] or None)
        if parent is not None and parent not in elements:
            raise ValueError(f"节点上级不存在：{element_id} → {parent}")
        parents[element_id] = parent
        if parent is not None:
            children.setdefault(parent, []).append(element_id)
    for child_ids in children.values():
        child_ids.sort()

    own_counts = {element_id: ZERO_COUNTS for element_id in elements}
    own_coverage = {element_id: {} for element_id in elements}
    for item in repositories:
        root = Path(item["path"]).resolve()
        owners = item["owners"]
        for file_path, owner in owners.items():
            category = owner.category
            if category not in ("code", "docs", "tests"):
                continue
            source_path = root / file_path
            try:
                content = source_path.read_bytes()
            except OSError as error:
                raise ValueError(f"无法读取归属文件以统计行数：{source_path}：{error}") from error
            own_counts[owner.element] = add_counts(
                own_counts[owner.element], count_file(file_path, category, content))
            if category == "code":
                covered, total, language = coverage_counts(
                    file_path, content.decode("utf-8", errors="ignore"))
                if language == "不适用":
                    own_coverage[owner.element] = add_coverage(
                        own_coverage[owner.element], {"不适用": (0, 0)})
                else:
                    own_coverage[owner.element] = add_coverage(
                        own_coverage[owner.element], {language: (covered, total)})

    totals = {}
    total_coverage = {}
    active = set()

    def calculate(element_id):
        if element_id in totals:
            return totals[element_id], total_coverage[element_id]
        if element_id in active:
            raise ValueError(f"仓图树出现循环层级：{element_id}")
        active.add(element_id)
        count = own_counts[element_id]
        coverage = own_coverage[element_id]
        for child_id in children.get(element_id, []):
            child_count, child_coverage = calculate(child_id)
            count = add_counts(count, child_count)
            coverage = add_coverage(coverage, child_coverage)
        active.remove(element_id)
        totals[element_id] = count
        total_coverage[element_id] = coverage
        return count, coverage

    for element_id in elements:
        calculate(element_id)

    map_nodes = {}
    page_nodes = {}
    for element_id in sorted(elements):
        element = elements[element_id]
        map_node = {
            "title": element.get("title", ""),
            "kind": element.get("kind", ""),
            "parent": parents[element_id],
            "own": _count_dict(own_counts[element_id]),
            "total": _count_dict(totals[element_id]),
        }
        map_nodes[element_id] = map_node
        page_nodes[element_id] = {
            **map_node,
            "description": _description(element),
            "coverage": coverage_text(total_coverage[element_id]),
        }

    top_roots = sorted(element_id for element_id, parent in parents.items() if parent is None)
    aliases = {root: placeholder for placeholder, root in repo_roots.items()}
    map_document = {"mode": mode, "nodes": map_nodes}
    page_document = {
        "mode": mode,
        "top_roots": top_roots,
        "nodes": page_nodes,
        "relations": sorted([
            {**relation, "container": _relation_container(
                relation["source"], relation["target"], parents, aliases)}
            for relation in relations
        ], key=lambda item: (item["source"], item["target"], item["title"])),
        "repo_roots": repo_roots,
        "drill_views": _drill_views(page_nodes, children, parents, relations, repo_roots),
    }
    return map_document, page_document
