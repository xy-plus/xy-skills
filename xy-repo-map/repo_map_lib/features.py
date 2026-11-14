"""Read feature descriptions and validate their paths against tracked files."""
import json
from pathlib import Path
import re
import sys


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "xy-handoff"))
from check_handoff import object_without_duplicate_keys  # noqa: E402


FEATURE_KEYS = {"id", "title", "what", "why", "directory", "items", "entries", "children"}
WHY_KEYS = {"text", "source", "quote", "from"}
ITEM_KEYS = {"id", "text", "status"}
FEATURE_ID = re.compile(r"^[a-z][a-z0-9-]*$")


def feature_chain(feature_id, features):
    """Yield a feature and its parents, starting at the feature itself."""
    current = feature_id
    while current is not None:
        yield current
        current = features[current]["parent"]


def is_feature_ancestor(ancestor_id, feature_id, features):
    """Return whether one feature is a strict ancestor of another."""
    return ancestor_id != feature_id and ancestor_id in feature_chain(feature_id, features)


def path_is_under(path, directory):
    """Return whether a repository-relative path belongs to a directory."""
    return directory == "" or path.startswith(directory + "/")


def directory_home(feature_id, features):
    """Return the nearest directory feature containing a feature."""
    return next(
        (item for item in feature_chain(feature_id, features)
         if features[item].get("directory") is not None),
        None,
    )


def deepest_directory(path, features, order):
    """Return the most specific directory feature containing a path."""
    matches = [
        feature_id for feature_id in order
        if features[feature_id].get("directory") is not None
        and path_is_under(path, features[feature_id]["directory"])
    ]
    return max(
        matches,
        key=lambda feature_id: len(features[feature_id]["directory"]),
        default=None,
    )


def _nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def _safe_relative_path(value, allow_directory_marker):
    if (not isinstance(value, str) or not value or "\0" in value
            or value.startswith("/") or "\\" in value):
        return False
    path = value[:-1] if allow_directory_marker and value.endswith("/") else value
    return all(part not in ("", ".", "..") for part in path.split("/"))


def _collect_features(value, parent, features, order, errors, label="根功能"):
    if not isinstance(value, dict):
        errors.append(f"{label}必须是对象")
        return
    allowed_keys = FEATURE_KEYS | (
        {"subrepositories", "external_dependencies"} if parent is None else set()
    )
    feature_id = value.get("id")
    current = feature_id if isinstance(feature_id, str) else label
    missing = allowed_keys - value.keys()
    extra = value.keys() - allowed_keys
    for key in sorted(missing):
        if key == "items":
            errors.append(f"功能 {current} 缺少键 items")
        else:
            errors.append(f"{label}缺少键 {key}")
    for key in sorted(extra):
        errors.append(f"{label}有多余键 {key}")

    if not isinstance(feature_id, str) or FEATURE_ID.fullmatch(feature_id) is None:
        errors.append(f"{label}的 id 必须匹配 ^[a-z][a-z0-9-]*$：{feature_id!r}")
    elif feature_id in features:
        errors.append(f"功能 id 重复：{feature_id}")
    else:
        features[feature_id] = {**value, "parent": parent}
        order.append(feature_id)

    if "items" in value:
        items = value["items"]
        if not isinstance(items, list):
            errors.append(f"功能 {current} 的 items 必须是数组")
        else:
            item_ids = set()
            for index, item in enumerate(items):
                item_label = f"功能 {current} 的 items[{index}]"
                if not isinstance(item, dict):
                    errors.append(f"{item_label} 必须是对象")
                    continue
                for key in sorted(ITEM_KEYS - item.keys()):
                    errors.append(f"{item_label}缺少键 {key}")
                for key in sorted(item.keys() - ITEM_KEYS):
                    errors.append(f"{item_label}有多余键 {key}")

                item_id = item.get("id")
                if not isinstance(item_id, str) or FEATURE_ID.fullmatch(item_id) is None:
                    errors.append(
                        f"{item_label}.id 必须匹配 ^[a-z][a-z0-9-]*$：{item_id!r}"
                    )
                elif item_id in item_ids:
                    errors.append(f"功能 {current} 的条目 id 重复：{item_id}")
                else:
                    item_ids.add(item_id)

                text = item.get("text")
                if (not _nonempty_text(text)
                        or any(character in text for character in ("\r", "\n", "\u2028", "\u2029"))):
                    errors.append(f"{item_label}.text 必须是非空单行字符串")
                if item.get("status") not in ("确认", "起草"):
                    errors.append(f"{item_label}.status 只能是 确认、起草")

    for key in ("title", "what"):
        if not _nonempty_text(value.get(key)):
            errors.append(f"{label}的 {key} 必须是非空字符串")

    why = value.get("why")
    if not isinstance(why, dict):
        errors.append(f"{label}的 why 必须是对象")
    else:
        for key in sorted(WHY_KEYS - why.keys()):
            errors.append(f"{label}的 why 缺少键 {key}")
        for key in sorted(why.keys() - WHY_KEYS):
            errors.append(f"{label}的 why 有多余键 {key}")
        source = why.get("source")
        if not _nonempty_text(why.get("text")):
            errors.append(f"{label}的 why.text 必须是非空字符串")
        if source not in ("原话", "确认", "起草"):
            errors.append(f"{label}的 why.source 只能是 原话、确认、起草")
        elif source == "原话":
            if not _nonempty_text(why.get("quote")) or not _nonempty_text(why.get("from")):
                errors.append(f"{label}的 why（原话）必须有非空 quote 和 from")
        elif source == "确认":
            if why.get("quote") != "" or not _nonempty_text(why.get("from")):
                errors.append(f"{label}的 why（确认）必须使用空 quote 和非空 from")
        elif why.get("quote") != "" or why.get("from") != "":
            errors.append(f"{label}的 why（起草）必须使用空 quote 和空 from")
        for key in ("quote", "from"):
            if key in why and not isinstance(why[key], str):
                errors.append(f"{label}的 why.{key} 必须是字符串")

    directory = value.get("directory")
    if directory is not None and not _safe_relative_path(directory, False) and directory != "":
        errors.append(f"{label}的 directory 必须是仓内相对目录或 null：{directory!r}")
    entries = value.get("entries")
    if not isinstance(entries, list) or any(not isinstance(entry, str) for entry in entries):
        errors.append(f"{label}的 entries 必须是字符串数组")
    elif any(not _safe_relative_path(entry, True) for entry in entries):
        errors.append(f"{label}的 entries 必须是安全的仓内相对路径")
    if parent is None:
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
    children = value.get("children")
    if not isinstance(children, list):
        errors.append(f"{label}的 children 必须是数组")
        return
    for index, child in enumerate(children):
        child_label = f"{label}的第 {index + 1} 个子功能"
        _collect_features(child, current, features, order, errors, child_label)


def _validate_paths(features, order, tracked, errors):
    if features[order[0]].get("directory") != "":
        errors.append("根功能的 directory 必须是空字符串")

    directory_owner = {}
    for feature_id in order:
        value = features[feature_id]
        directory = value.get("directory")
        if directory is None:
            continue
        if directory in directory_owner:
            errors.append(f"两个功能不能共用 directory {directory!r}：{directory_owner[directory]} 和 {feature_id}")
        else:
            directory_owner[directory] = feature_id
        if not any(path_is_under(path, directory) for path in tracked):
            errors.append(f"功能 {feature_id} 的 directory 下没有被跟踪的文件：{directory!r}")
        ancestor = next(
            (candidate for candidate in feature_chain(feature_id, features)
             if candidate != feature_id and features[candidate].get("directory") is not None),
            None,
        )
        if ancestor is not None:
            parent_directory = features[ancestor]["directory"]
            if not path_is_under(directory, parent_directory):
                errors.append(f"功能 {feature_id} 的 directory 不在祖先目录内：{directory!r}")

        children = value.get("children", [])
        if not children and value.get("entries"):
            errors.append(f"没有子功能的目录功能 {feature_id} 的 entries 必须是空数组")

    for feature_id in order:
        value = features[feature_id]
        entries = value.get("entries")
        if not isinstance(entries, list):
            continue
        home = directory_home(feature_id, features)
        for entry in entries:
            is_directory = entry.endswith("/")
            clean_entry = entry[:-1] if is_directory else entry
            if is_directory:
                matched = [path for path in tracked if path_is_under(path, clean_entry)]
            else:
                matched = [entry] if entry in tracked else []
            if not matched:
                errors.append(f"功能 {feature_id} 的 entries 不存在或没有被跟踪文件：{entry}")
                continue
            for path in matched:
                if home is None or deepest_directory(path, features, order) != home:
                    errors.append(f"功能 {feature_id} 的入口不在所属目录功能内：{path}")
                    break


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

    features, order, errors = {}, [], []
    _collect_features(document, None, features, order, errors)
    if not order or document.get("id") not in features:
        return features, order, errors or ["功能清单必须包含根功能"]
    if errors:
        return features, order, errors
    _validate_paths(features, order, tracked_paths, errors)
    return features, order, errors
