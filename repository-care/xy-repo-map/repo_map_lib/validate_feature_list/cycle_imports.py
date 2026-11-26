"""Compare registered cycle imports across a reviewed change."""
import json
import subprocess

from .features import features_by_id


FEATURES_PATH = "repo-map/features.json"
MISSING = object()


def _run_git(repository, *arguments):
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _git(repository, *arguments):
    result = _run_git(repository, *arguments)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git 命令失败")
    return result.stdout


def _feature_map(repository, commit):
    result = _run_git(repository, "show", f"{commit}:{FEATURES_PATH}")
    if result.returncode:
        return None
    try:
        document = json.loads(result.stdout)
    except ValueError as error:
        raise ValueError(f"{commit} 的 {FEATURES_PATH} 不是有效 JSON：{error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"{commit} 的 {FEATURES_PATH} 根记录必须是对象")
    return document


def _cycle_imports(document, commit):
    imports = document.get("existing_cycle_imports", MISSING)
    if imports is MISSING:
        return MISSING
    if not isinstance(imports, list):
        raise ValueError(
            f"{commit} 的 existing_cycle_imports 必须是数组；名单只能删不能加"
        )
    edges = set()
    for item in imports:
        if (
            not isinstance(item, dict)
            or item.keys() != {"from", "to"}
            or not isinstance(item.get("from"), str)
            or not isinstance(item.get("to"), str)
        ):
            raise ValueError(
                f"{commit} 的 existing_cycle_imports 条目格式无效；名单只能删不能加"
            )
        edges.add((item["from"], item["to"]))
    return edges


def _first_registration_errors(repository, base, head, old_document, new_document):
    commits = int(_git(repository, "rev-list", "--count", f"{base}..{head}").strip())
    changed_paths = set(
        filter(
            None,
            _git(repository, "diff", "--name-only", "-z", base, head).split("\0"),
        )
    )
    old_without_registry = dict(old_document)
    new_without_registry = dict(new_document)
    old_without_registry.pop("existing_cycle_imports", None)
    new_without_registry.pop("existing_cycle_imports", None)
    if (
        commits == 1
        and changed_paths == {FEATURES_PATH}
        and old_without_registry == new_without_registry
    ):
        return []
    return [
        "首次登记非空 existing_cycle_imports 必须单独一个提交，且只改这个字段；"
        "名单只能删不能加。"
    ]


def _base_feature_id(feature_id, current_features, old_features):
    if feature_id in old_features:
        return feature_id
    current = current_features.get(feature_id)
    if current is None or not isinstance(current.get("directory"), str):
        return None
    matching_ids = [
        old_id
        for old_id, old in old_features.items()
        if old.get("directory") == current["directory"]
    ]
    return matching_ids[0] if len(matching_ids) == 1 else None


def cycle_registry_change_errors(repository, base, head):
    """Return violations of the rule that the existing cycle list can only shrink."""
    old_document = _feature_map(repository, base)
    new_document = _feature_map(repository, head)
    if new_document is None:
        return []

    old_edges = (
        _cycle_imports(old_document, base) if old_document is not None else MISSING
    )
    new_edges = _cycle_imports(new_document, head)
    if old_edges is MISSING:
        if new_edges is MISSING or not new_edges:
            return []
        if old_document is None:
            return [
                "首次登记非空 existing_cycle_imports 没有基准功能图可对照；"
                "名单只能删不能加。"
            ]
        return _first_registration_errors(
            repository, base, head, old_document, new_document
        )
    if new_edges is MISSING:
        return ["送审功能图缺 existing_cycle_imports；名单只能删不能加。"]

    old_features = features_by_id(old_document)
    current_features = features_by_id(new_document)
    errors = []
    for source, target in sorted(new_edges):
        old_source = _base_feature_id(source, current_features, old_features)
        old_target = _base_feature_id(target, current_features, old_features)
        if old_source is None or old_target is None or (old_source, old_target) not in old_edges:
            errors.append(
                f"新增 existing_cycle_imports 条目 {source} → {target}；名单只能删不能加。"
            )
    return errors
