from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPOSITORY_ROOT))
from json_validation import object_without_duplicate_keys  # noqa: E402


FEATURE_KEYS = {"id", "title", "what", "why", "directory", "items", "children"}
ROOT_REQUIRED_KEYS = {
    "subrepositories",
    "external_dependencies",
    "existing_cycle_imports",
}
ROOT_OPTIONAL_KEYS = {"used_by_repositories"}
WHY_KEYS = {"text", "source", "quote", "from"}
ITEM_KEYS = {"id", "text", "status"}
FEATURE_ID = re.compile(r"^[a-z][a-z0-9-]*$")
ANSWERS_JSON_REF = re.compile(
    r"^.+/answers\.json#answers\.[A-Za-z0-9_-]+(?:,answers\.[A-Za-z0-9_-]+)*(?:（[^（）]*）)?$"
)


def feature_chain(feature_id, features):
    """Yield a feature and its parents, starting at the feature itself."""
    current = feature_id
    while current is not None:
        yield current
        current = features[current]["parent"]


def features_by_id(document):
    """Return a nested feature document indexed by feature id."""
    result = {}
    pending = [document]
    while pending:
        feature = pending.pop()
        if not isinstance(feature, dict):
            continue
        feature_id = feature.get("id")
        if isinstance(feature_id, str):
            result[feature_id] = feature
        children = feature.get("children")
        if isinstance(children, list):
            pending.extend(reversed(children))
    return result


def is_feature_ancestor(ancestor_id, feature_id, features):
    """Return whether one feature is a strict ancestor of another."""
    return ancestor_id != feature_id and ancestor_id in feature_chain(
        feature_id, features
    )


def path_is_under(path, directory):
    """Return whether a repository-relative path belongs to a directory."""
    return directory == "" or path.startswith(directory + "/")


def deepest_directory(path, features, order):
    """Return the feature owning a path by its deepest containing directory."""
    return max(
        (
            feature_id
            for feature_id in order
            if path_is_under(path, features[feature_id]["directory"])
        ),
        key=lambda feature_id: len(features[feature_id]["directory"]),
    )


def _nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def _safe_relative_path(value, allow_directory_marker):
    if (
        not isinstance(value, str)
        or not value
        or "\0" in value
        or value.startswith("/")
        or "\\" in value
    ):
        return False
    path = value[:-1] if allow_directory_marker and value.endswith("/") else value
    return all(part not in ("", ".", "..") for part in path.split("/"))


def _validate_feature_keys(value, allowed_keys, current, label, errors):
    missing = allowed_keys - ROOT_OPTIONAL_KEYS - value.keys()
    extra = value.keys() - allowed_keys
    for key in sorted(missing):
        if key == "items":
            errors.append(f"功能 {current} 缺少键 items")
        elif key == "directory":
            errors.append(
                f"功能 {current} 缺少键 directory；请填写该功能自己的仓内目录路径。"
            )
        elif key == "existing_cycle_imports":
            errors.append("根功能缺 existing_cycle_imports")
        else:
            errors.append(f"{label}缺少键 {key}")
    for key in sorted(extra):
        if key == "entries":
            errors.append(
                f"功能 {current}（directory {value.get('directory')!r}）不再支持 entries；"
                "请删除该字段并将文件放入对应功能目录。"
            )
        else:
            errors.append(f"{label}有多余键 {key}")


def _validate_items(value, current, errors):
    if "items" not in value:
        return
    items = value["items"]
    if not isinstance(items, list):
        errors.append(f"功能 {current} 的 items 必须是数组")
        return
    item_ids = set()
    for index, item in enumerate(items):
        _validate_item(item, index, current, item_ids, errors)


def _validate_item(item, index, current, item_ids, errors):
    item_label = f"功能 {current} 的 items[{index}]"
    if not isinstance(item, dict):
        errors.append(f"{item_label} 必须是对象")
        return
    for key in sorted(ITEM_KEYS - item.keys()):
        errors.append(f"{item_label}缺少键 {key}")
    for key in sorted(item.keys() - ITEM_KEYS):
        errors.append(f"{item_label}有多余键 {key}")
    item_id = item.get("id")
    if not isinstance(item_id, str) or FEATURE_ID.fullmatch(item_id) is None:
        errors.append(f"{item_label}.id 必须匹配 ^[a-z][a-z0-9-]*$：{item_id!r}")
    elif item_id in item_ids:
        errors.append(f"功能 {current} 的条目 id 重复：{item_id}")
    else:
        item_ids.add(item_id)
    text = item.get("text")
    if not _nonempty_text(text) or any(
        character in text for character in ("\r", "\n", "\u2028", "\u2029")
    ):
        errors.append(f"{item_label}.text 必须是非空单行字符串")
    if item.get("status") not in ("确认", "起草"):
        errors.append(f"{item_label}.status 只能是 确认、起草")


def _validate_feature_text(value, label, errors):
    for key in ("title", "what"):
        if not _nonempty_text(value.get(key)):
            errors.append(f"{label}的 {key} 必须是非空字符串")

    why = value.get("why")
    if not isinstance(why, dict):
        errors.append(f"{label}的 why 必须是对象")
    else:
        _validate_why(value, why, label, errors)


def _validate_why(value, why, label, errors):
    for key in sorted(WHY_KEYS - why.keys()):
        errors.append(f"{label}的 why 缺少键 {key}")
    for key in sorted(why.keys() - WHY_KEYS):
        errors.append(f"{label}的 why 有多余键 {key}")
    if not _nonempty_text(why.get("text")):
        errors.append(f"{label}的 why.text 必须是非空字符串")
    _validate_why_source(why, label, errors)
    _validate_why_reference(value, why, label, errors)


def _validate_why_source(why, label, errors):
    source = why.get("source")
    if source not in ("原话", "确认", "起草"):
        errors.append(f"{label}的 why.source 只能是 原话、确认、起草")
    elif source == "原话":
        _validate_quote_origin(why, label, errors)
    elif source == "确认":
        if why.get("quote") != "" or not _nonempty_text(why.get("from")):
            errors.append(f"{label}的 why（确认）必须使用空 quote 和非空 from")
    elif why.get("quote") != "" or why.get("from") != "":
        errors.append(f"{label}的 why（起草）必须使用空 quote 和空 from")


def _validate_quote_origin(why, label, errors):
    if not _nonempty_text(why.get("quote")) or not _nonempty_text(why.get("from")):
        errors.append(f"{label}的 why（原话）必须有非空 quote 和 from")


def _validate_why_reference(value, why, label, errors):
    for key in ("quote", "from"):
        if key in why and not isinstance(why[key], str):
            errors.append(f"{label}的 why.{key} 必须是字符串")
    origin = why.get("from")
    if (
        isinstance(origin, str)
        and "answers.json" in origin
        and not ANSWERS_JSON_REF.fullmatch(origin)
    ):
        errors.append(
            f"功能 {value.get('id')} 的 why.from 中 answers.json 必须引用 JSON 回答键：{origin!r}"
        )


def _validate_feature_paths(value, parent, current, label, errors):
    directory = value.get("directory")
    if "directory" in value:
        if not isinstance(directory, str):
            actual_directory = "null" if directory is None else repr(directory)
            errors.append(
                f"功能 {current} 的 directory 必须是字符串且不能为 null，"
                f"目前为 {actual_directory}；"
                "请为该功能指定自己的仓内目录。"
            )
        elif parent is None and directory != "":
            errors.append(
                f"根功能 {current} 的 directory 必须是空字符串，目前为 {directory!r}；"
                '请改为 ""。'
            )
        elif parent is not None and not _safe_relative_path(directory, False):
            errors.append(
                f"功能 {current} 的 directory {directory!r} 必须是安全的仓内相对目录路径；"
                "请改为非空、无绝对路径、反斜线、空段、. 或 .. 的目录路径。"
            )
    if parent is None:
        _validate_root_paths(value, label, errors)


def _validate_root_paths(value, label, errors):
    subrepositories = value.get("subrepositories")
    if not isinstance(subrepositories, list) or any(
        not isinstance(path, str) for path in subrepositories
    ):
        errors.append(f"{label}的 subrepositories 必须是字符串数组")
    else:
        for path in subrepositories:
            if not _safe_relative_path(path, False):
                errors.append(
                    f"{label}的 subrepositories 必须是安全的仓内相对目录路径：{path!r}"
                )
    external_dependencies = value.get("external_dependencies")
    if not isinstance(external_dependencies, list) or any(
        not isinstance(name, str) for name in external_dependencies
    ):
        errors.append(f"{label}的 external_dependencies 必须是字符串数组")
    else:
        for name in external_dependencies:
            if not name or name.strip() != name or len(name.splitlines()) != 1:
                errors.append(
                    f"{label}的 external_dependencies 名字必须是非空字符串、首尾无空白且不含换行：{name!r}"
                )
    _validate_used_by_repositories(value, label, errors)
    _validate_existing_cycle_imports(value, label, errors)


def _validate_existing_cycle_imports(value, label, errors):
    if "existing_cycle_imports" not in value:
        return
    imports = value["existing_cycle_imports"]
    if not isinstance(imports, list):
        errors.append(f"{label}的 existing_cycle_imports 必须是数组")
        return

    seen = set()
    for index, item in enumerate(imports):
        errors.extend(_existing_cycle_import_item_errors(item, index, label, seen))


def _existing_cycle_import_item_errors(item, index, label, seen):
    item_label = f"{label}的 existing_cycle_imports[{index}]"
    if not isinstance(item, dict):
        return [f"{item_label}必须是对象"]
    errors = []
    if item.keys() != {"from", "to"}:
        errors.append(f"{item_label}必须恰有 from、to 两个键")
    source = item.get("from")
    target = item.get("to")
    if not isinstance(source, str) or not source.strip():
        errors.append(f"{item_label}.from 必须是非空字符串")
    if not isinstance(target, str) or not target.strip():
        errors.append(f"{item_label}.to 必须是非空字符串")
    if isinstance(source, str) and isinstance(target, str):
        _validate_cycle_import_edge(source, target, item_label, seen, errors)
    return errors


def _validate_cycle_import_edge(source, target, item_label, seen, errors):
    edge = (source, target)
    if source == target:
        errors.append(f"{item_label}不能自己引用：{source}")
    if edge in seen:
        errors.append(f"{item_label}不能重复：{source} → {target}")
    seen.add(edge)


def _validate_used_by_repositories(value, label, errors):
    if "used_by_repositories" not in value:
        return
    repositories = value["used_by_repositories"]
    if not isinstance(repositories, list) or any(
        not isinstance(path, str) for path in repositories
    ):
        errors.append(f"{label}的 used_by_repositories 必须是字符串数组")
        return

    seen = set()
    for index, path in enumerate(repositories):
        path_label = f"{label}的 used_by_repositories[{index}]"
        if not path or path.strip() != path:
            errors.append(f"{path_label}必须是非空且首尾无空白的字符串：{path!r}")
        if any(character in path for character in ("\n", "\r", "\u2028", "\u2029")):
            errors.append(f"{path_label}不能含换行：{path!r}")
        if path.startswith("/") or path == ".":
            errors.append(f"{path_label}必须是相对路径且不能是 '.'：{path!r}")
        if "\\" in path:
            errors.append(f"{path_label}不能含反斜线：{path!r}")
        if path in seen:
            errors.append(f"{path_label}不能重复：{path!r}")
        seen.add(path)


@dataclass
class FeatureCollection:
    features: dict
    order: list
    errors: list


def _collect_features(value, parent, collection, label):
    features = collection.features
    order = collection.order
    errors = collection.errors
    if not isinstance(value, dict):
        errors.append(f"{label}必须是对象")
        return
    root_keys = ROOT_REQUIRED_KEYS | ROOT_OPTIONAL_KEYS if parent is None else set()
    allowed_keys = FEATURE_KEYS | root_keys
    feature_id = value.get("id")
    current = feature_id if isinstance(feature_id, str) else label
    _validate_feature_keys(value, allowed_keys, current, label, errors)

    if not isinstance(feature_id, str) or FEATURE_ID.fullmatch(feature_id) is None:
        errors.append(f"{label}的 id 必须匹配 ^[a-z][a-z0-9-]*$：{feature_id!r}")
    elif feature_id in features:
        errors.append(f"功能 id 重复：{feature_id}")
    else:
        features[feature_id] = {**value, "parent": parent}
        order.append(feature_id)

    _validate_items(value, current, errors)
    _validate_feature_text(value, label, errors)
    _validate_feature_paths(value, parent, current, label, errors)
    children = value.get("children")
    if not isinstance(children, list):
        errors.append(f"{label}的 children 必须是数组")
        return
    for index, child in enumerate(children):
        child_label = f"{label}的第 {index + 1} 个子功能"
        _collect_features(child, current, collection, child_label)


def _validate_paths(features, order, tracked, errors):
    directory_owner = {}
    for feature_id in order:
        value = features[feature_id]
        directory = value.get("directory")
        if directory in directory_owner:
            errors.append(
                f"功能 {feature_id} 的 directory {directory!r} 与功能 "
                f"{directory_owner[directory]} 的目录重复；请为 {feature_id} 指定独立目录。"
            )
        else:
            directory_owner[directory] = feature_id
        if not any(path_is_under(path, directory) for path in tracked):
            errors.append(
                f"功能 {feature_id} 的 directory {directory!r} 下没有 Git 跟踪文件；"
                f"请把 {feature_id} 的文件放进目录 {directory!r} 并提交，或把 {feature_id} 的 "
                "directory 改为其文件实际所在的目录。"
            )

    for feature_id in order:
        parent_id = features[feature_id]["parent"]
        if parent_id is None:
            continue
        directory = features[feature_id]["directory"]
        parent_directory = features[parent_id]["directory"]
        if not path_is_under(directory, parent_directory):
            errors.append(
                f"功能 {feature_id} 的 directory {directory!r} 不在上级功能 "
                f"{parent_id} 的 directory {parent_directory!r} 内；"
                f"请把 {feature_id} 的目录移到 {parent_directory!r} 下。"
            )
            continue

        nearest = max(
            (
                candidate
                for candidate in order
                if candidate != feature_id
                and path_is_under(directory, features[candidate]["directory"])
            ),
            key=lambda candidate: len(features[candidate]["directory"]),
            default=None,
        )
        if nearest != parent_id:
            nearest_directory = features[nearest]["directory"] if nearest else None
            errors.append(
                f"功能 {feature_id} 的 directory {directory!r} 最近的功能目录是 "
                f"{nearest} 的 directory {nearest_directory!r}，不是上级功能 "
                f"{parent_id} 的 directory {parent_directory!r}；"
                "请调整功能树或目录嵌套，使上级目录成为最近的功能祖先。"
            )


def load_features(repository, tracked_paths):
    """Return the ordered feature tree after checking its complete path contract."""
    root = Path(repository)
    path = root / "repo-map" / "features.json"
    if not path.is_file():
        return {}, [], ["缺少 repo-map/features.json"]
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_without_duplicate_keys,
        )
    except (OSError, UnicodeError, ValueError) as error:
        return {}, [], [f"无法读取 repo-map/features.json：{error}"]

    collection = FeatureCollection({}, [], [])
    _collect_features(document, None, collection, "根功能")
    features, order, errors = collection.features, collection.order, collection.errors
    if not order or document.get("id") not in features:
        return features, order, errors or ["功能清单必须包含根功能"]
    missing_cycle_registry = "根功能缺 existing_cycle_imports"
    if errors and any(error != missing_cycle_registry for error in errors):
        return features, order, errors
    _validate_paths(features, order, tracked_paths, errors)
    return features, order, errors
