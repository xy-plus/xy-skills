"""Full validation for one repository's repo-map directory."""
import json
from pathlib import Path

from .approval import recorded_digests
from .constants import CODE_SUFFIXES
from .git_repository import (discover_subrepositories, gitlink_paths,
                             require_repository_root, tracked_paths)
from .likec4 import export_model, validate_likec4
from .model import model_problems, source_file_problems
from .ownership import category_for_path, check_ownership
from .structure import structure_digest


def check_repository(repository, check_approval):
    """Return (problem lines, exported model); LikeC4 syntax must validate first."""
    root = require_repository_root(repository)
    repo_map = root / "repo-map"
    problems = []
    if not repo_map.is_dir():
        return [f"缺少仓图目录：{repo_map}"], None
    config_path = repo_map / "likec4.config.json"
    kinds_path = repo_map / "kinds.c4"
    approvals_path = repo_map / "approvals.txt"
    for path in (config_path, kinds_path, approvals_path):
        if not path.is_file():
            problems.append(f"缺少仓图文件：{path.name}")
    model_files = [path for path in repo_map.rglob("*.c4") if path != kinds_path]
    if not model_files:
        problems.append("repo-map 至少需要一个模型 .c4 文件")
    try:
        problems.extend(source_file_problems(repo_map))
    except (OSError, UnicodeError) as error:
        problems.append(f"无法读取 .c4 文件：{error}")
    if problems:
        return problems, None

    validation = validate_likec4(repo_map)
    if validation.returncode != 0:
        details = (validation.stdout + validation.stderr).strip()
        problems.append(f"LikeC4 validate 失败：{details}")
        return problems, None
    try:
        model = export_model(repo_map)
    except ValueError as error:
        return [f"LikeC4 export json 失败：{error}"], None

    problems.extend(model_problems(model))
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        problems.append(f"likec4.config.json 无法读取：{error}")
        config = {}
    if not isinstance(config, dict) or set(config) != {"name"} or not isinstance(config.get("name"), str):
        problems.append("likec4.config.json 只能包含 name")
    else:
        root_ids = [element_id for element_id in model.get("elements", {}) if "." not in element_id]
        if len(root_ids) == 1 and config["name"] != root_ids[0]:
            problems.append("likec4.config.json 的 name 必须与根节点 id 相同")

    tracked = tracked_paths(root)
    gitlinks = gitlink_paths(root)
    subrepositories = discover_subrepositories(root)
    ownership_problems, owners = check_ownership(root, model, tracked, gitlinks, subrepositories)
    problems.extend(ownership_problems)
    for file_path, owner in owners.items():
        if category_for_path(root, model, file_path) == "code":
            suffix = Path(file_path).suffix.lower()
            if suffix not in CODE_SUFFIXES:
                problems.append(f"code 文件后缀不支持：{file_path}")

    if check_approval:
        try:
            digests = recorded_digests(approvals_path)
            digest = structure_digest(model)
            if digest not in digests:
                problems.append(f"图未经同意：repo-map sha256 {digest}")
        except ValueError as error:
            problems.append(str(error))
    return problems, model
