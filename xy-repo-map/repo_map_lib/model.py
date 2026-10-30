"""Rules for the restricted LikeC4 model vocabulary."""
from pathlib import Path
import re


ELEMENT_KEYS = {"id", "kind", "title", "description", "links", "style"}
RELATION_KEYS = {"id", "source", "target", "title"}
LINK_KEYS = {"url", "title", "relative"}
CATEGORIES = {"code", "tests", "docs", "data", "repo"}


def _description(element):
    description = element.get("description")
    if isinstance(description, dict):
        return description.get("txt", "")
    return description


def model_problems(model):
    """Return violations of the node, description, relation, and link rules."""
    problems = []
    elements = model.get("elements", {})
    if not isinstance(elements, dict):
        return ["LikeC4 导出中的 elements 必须是对象"]
    if not isinstance(model.get("relations", {}), dict):
        return ["LikeC4 导出中的 relations 必须是对象"]

    roots = [element_id for element_id in elements if "." not in element_id]
    if len(roots) != 1 or any(not isinstance(elements.get(root), dict)
                              or elements[root].get("kind") != "node" for root in roots):
        problems.append("顶层必须恰好有一个 node 根元素")

    for element_id, element in elements.items():
        where = f"元素 {element_id}"
        if not isinstance(element, dict):
            problems.append(f"{where} 必须是对象")
            continue
        for key in sorted(element.keys() - ELEMENT_KEYS):
            problems.append(f"{where} 带了不允许的属性：{key}")
        if element.get("style") not in ({}, None):
            problems.append(f"{where} 带了不允许的 style 属性")
        if element.get("kind") not in ("node", "repo"):
            problems.append(f"{where} 类型 {element.get('kind')} 不合法，只能是 node 或 repo")
        if element.get("id") != element_id:
            problems.append(f"{where} 的 id 与模型键不一致")
        for field in ("title",):
            if not isinstance(element.get(field), str) or not element[field].strip():
                problems.append(f"{where} 缺少有效的 {field}")
        description = _description(element)
        if not isinstance(description, str) or not description.strip():
            problems.append(f"{where} 缺少有效的 description")
        if "." in element_id:
            parent = element_id.rpartition(".")[0]
            if parent not in elements:
                problems.append(f"{where} 的上级元素 {parent} 不存在")
            elif isinstance(elements[parent], dict) and elements[parent].get("kind") == "repo":
                problems.append(f"repo 元素不能有子元素：{parent}")
        links = element.get("links", [])
        if not isinstance(links, list):
            problems.append(f"{where} 的 links 必须是数组")
            continue
        repo_links = 0
        for link in links:
            if not isinstance(link, dict):
                problems.append(f"{where} 的 link 必须是对象")
                continue
            for key in sorted(link.keys() - LINK_KEYS):
                problems.append(f"{where} 的 link 带了不允许的属性：{key}")
            url = link.get("url")
            category = link.get("title")
            if not isinstance(url, str) or not url.strip():
                problems.append(f"{where} 的 link 缺少路径")
            if not isinstance(category, str) or category not in CATEGORIES:
                problems.append(f"{where} 的 link 类别只能是 code、tests、docs、data、repo")
            if category == "repo":
                repo_links += 1
            if element.get("kind") == "repo" and category != "repo":
                problems.append(f"repo 元素 {element_id} 只能有 repo 链接")
        if element.get("kind") == "repo" and repo_links != 1:
            problems.append(f"repo 元素 {element_id} 必须有恰好一个 repo 链接")
        if element.get("kind") == "node" and repo_links:
            problems.append(f"node 元素 {element_id} 不能有 repo 链接")

    for relation_id, relation in model.get("relations", {}).items():
        where = f"关系 {relation_id}"
        if not isinstance(relation, dict):
            problems.append(f"{where} 必须是对象")
            continue
        for key in sorted(relation.keys() - RELATION_KEYS):
            problems.append(f"{where} 带了不允许的属性：{key}")
        for side in ("source", "target"):
            endpoint = relation.get(side)
            if isinstance(endpoint, dict):
                endpoint = endpoint.get("model")
            if not isinstance(endpoint, str) or endpoint not in elements:
                problems.append(f"{where} 的 {side} 元素不存在")
            elif isinstance(elements[endpoint], dict) and elements[endpoint].get("kind") == "repo":
                continue
        if not isinstance(relation.get("title"), str) or not relation["title"].strip():
            problems.append(f"{where} 缺少有效的标题")
    return problems


def source_file_problems(repo_map):
    """Check the single standard specification file and its exact contents."""
    from .constants import STANDARD_KINDS

    repo_map = Path(repo_map)
    problems = []
    kinds = repo_map / "kinds.c4"
    if not kinds.is_file():
        problems.append("缺少 kinds.c4")
    elif kinds.read_text(encoding="utf-8") != STANDARD_KINDS:
        problems.append("kinds.c4 与 repo_map.py 中的标准版本不一致")
    spec_pattern = re.compile(r"\bspecification\s*\{", re.MULTILINE)
    for path in sorted(repo_map.rglob("*.c4")):
        if path == kinds:
            continue
        if spec_pattern.search(path.read_text(encoding="utf-8")):
            problems.append(f"specification 块只允许出现在 kinds.c4：{path.relative_to(repo_map)}")
    return problems
