"""Generate compact, clickable scoped views for a combined repository model."""
import hashlib
import re

from .metrics import LineCounts


def view_id(element_id):
    readable = re.sub(r"[^a-zA-Z0-9_]", "_", element_id)
    suffix = hashlib.sha256(element_id.encode("utf-8")).hexdigest()[:8]
    return f"view_{readable}_{suffix}"


def _quote(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ") + "'"


def _text_description(element):
    description = element.get("description", "")
    if isinstance(description, dict):
        return description.get("txt", "")
    return description


def _count_text(counts):
    return (f"代码 {counts.code:,} · 文档 {counts.docs:,} · 注释 {counts.comments:,} "
            f"· 测试 {counts.tests:,}")


def _parent_id(element_id):
    return element_id.rpartition(".")[0] or None


def _include(element_id, counts, destination):
    properties = [f"technology {_quote(_count_text(counts))}"]
    if destination is not None:
        properties.append(f"navigateTo {destination}")
    lines = [f"    include {element_id} with {{"]
    lines.extend(f"      {property_text}" for property_text in properties)
    lines.append("    }")
    return lines


def generate_views(model, top_root_ids, mode, totals, repo_targets, coverage):
    """Generate an index and one scoped view for every element with children."""
    if mode not in ("草稿", "正式"):
        raise ValueError("mode 只能是 草稿 或 正式")
    elements = model.get("elements", {})
    prefix = "草稿·未经同意 " if mode == "草稿" else ""
    lines = ["views {", "  view index {", f"    title {_quote(prefix + '仓库总览')}"]
    for root_id in top_root_ids:
        lines.extend(_include(root_id, totals[root_id], None))
    lines.extend(["  }"])

    children = {}
    for element_id in elements:
        parent = _parent_id(element_id)
        if parent is not None:
            children.setdefault(parent, []).append(element_id)
    for parent_id in sorted(children):
        parent = elements[parent_id]
        title = parent.get("title", parent_id)
        lines.extend([f"  view {view_id(parent_id)} of {parent_id} {{",
                      f"    title {_quote(prefix + title)}"])
        for child_id in sorted(children[parent_id]):
            destination = view_id(repo_targets[child_id]) if child_id in repo_targets else None
            lines.extend(_include(child_id, totals[child_id], destination))
        lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n\n" + coverage_descriptions(model, coverage)


def coverage_descriptions(model, coverage):
    """Append computed coverage to element descriptions in a generated extend block."""
    lines = ["model {"]
    for element_id in sorted(model.get("elements", {})):
        description = _text_description(model["elements"][element_id])
        value = coverage.get(element_id, "不适用")
        lines.extend([f"  extend {element_id} {{",
                      f"    description {_quote(description + ' 文档覆盖率：' + value)}",
                      "  }"])
    lines.extend(["}", ""])
    return "\n".join(lines)


def add_counts(first, second):
    return LineCounts(first.code + second.code, first.docs + second.docs,
                      first.comments + second.comments, first.tests + second.tests)


def add_coverage(first, second):
    result = dict(first)
    for language, value in second.items():
        if language not in result:
            result[language] = value
        else:
            old_covered, old_total = result[language]
            new_covered, new_total = value
            result[language] = (old_covered + new_covered, old_total + new_total)
    return result


def coverage_text(items):
    if not items:
        return "不适用"
    parts = []
    for language in sorted(items):
        if language == "不适用":
            parts.append("不适用")
        else:
            covered, total = items[language]
            parts.append(f"{language} {covered}/{total}")
    return "；".join(parts)
