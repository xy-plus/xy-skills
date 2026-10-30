"""Create the starting LikeC4 map without overwriting an existing draft."""
import json
from pathlib import Path
import re

from .constants import STANDARD_KINDS
from .git_repository import ArgumentError, discover_subrepositories, require_repository_root


def _quoted(value, label):
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ArgumentError(f"{label} 必须是非空单行文本")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _repository_id(name, used):
    part = re.sub(r"[^a-z0-9_]", "_", name.lower())
    if not part or not part[0].isalpha():
        part = "repo_" + part
    base = part
    number = 2
    while part in used:
        part = f"{base}_{number}"
        number += 1
    used.add(part)
    return part


def init_map(repository, root_id, title, description):
    """Create config, fixed kinds, empty approvals, and root/subrepository nodes."""
    root = require_repository_root(repository)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", root_id):
        raise ArgumentError("--id 必须是字母开头且只含字母、数字、下划线的 LikeC4 id")
    quoted_title = _quoted(title, "--title")
    quoted_description = _quoted(description, "--description")
    destination = root / "repo-map"
    if destination.exists():
        raise ValueError(f"repo-map 已经存在，拒绝覆盖：{destination}")

    children = discover_subrepositories(root)
    used = set()
    child_rows = []
    for child in children:
        name = Path(child).name
        child_id = _repository_id(name, used)
        child_rows.extend([
            f"    {child_id} = repo {_quoted(name, '子仓标题')} {{",
            "      description '子仓占位；内部功能由子仓自己的仓图描述。'",
            f"      link ../{child.rstrip('/')}/ 'repo'",
            "    }",
        ])

    model_lines = ["model {", f"  {root_id} = node {quoted_title} {{",
                   f"    description {quoted_description}"]
    model_lines.extend(child_rows)
    model_lines.extend(["  }", "}", ""])
    destination.mkdir()
    (destination / "likec4.config.json").write_text(
        json.dumps({"name": root_id}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "kinds.c4").write_text(STANDARD_KINDS, encoding="utf-8")
    (destination / "approvals.txt").write_text("", encoding="utf-8")
    (destination / "model.c4").write_text("\n".join(model_lines), encoding="utf-8")
    return destination
