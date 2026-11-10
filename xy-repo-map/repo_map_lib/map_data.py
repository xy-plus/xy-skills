"""Derive feature ownership, line totals, dependencies, and review markers."""
from collections import defaultdict
from pathlib import Path

from .constants import (
    CODE_SUFFIXES,
    FILE_GROUPS,
    LINE_NAMES,
    MARKER_NAMES,
    TEST_DIRECTORY_NAMES,
    UNSUPPORTED_CODE_SUFFIXES,
)
from .dependencies import directory_mentions, file_mentions, imports_for_file
from .features import load_features
from .git_repository import discover_subrepositories, require_repository_root, tracked_paths
from .metrics import count_file
from .ownership import assign_files


SOURCE_SUFFIXES = set(CODE_SUFFIXES) | {".html", ".css"}
TEST_FILE_ENDINGS = ("_test.py", "_test.rs")


def _is_test_file(path):
    suffix = Path(path).suffix.lower()
    name = Path(path).name
    has_code_suffix = suffix in SOURCE_SUFFIXES
    in_test_directory = any(part in TEST_DIRECTORY_NAMES for part in Path(path).parts[:-1])
    named_test = (
        (name.startswith("test_") and suffix in (".py", ".rs"))
        or name in ("conftest.py",)
        or name.endswith(TEST_FILE_ENDINGS)
    )
    return has_code_suffix and (in_test_directory or named_test)


def _file_category(path):
    if any(part in ("third_party", "vendor", "node_modules") for part in Path(path).parts):
        return "other"
    if _is_test_file(path):
        return "tests"
    suffix = Path(path).suffix.lower()
    if suffix in SOURCE_SUFFIXES:
        return "code"
    if suffix in (".md", ".rst") and not any(
        part in TEST_DIRECTORY_NAMES for part in Path(path).parts[:-1]
    ):
        return "docs"
    return "other"


def _source_text(repository, path):
    try:
        content = (Path(repository) / path).read_bytes()
    except OSError:
        return None
    if b"\0" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _entry_files(features, order, paths):
    result = {feature_id: set() for feature_id in order}
    path_set = set(paths)
    for feature_id in order:
        for entry in features[feature_id]["entries"]:
            if entry.endswith("/"):
                prefix = entry
                result[feature_id].update(path for path in paths if path.startswith(prefix))
            elif entry in path_set:
                result[feature_id].add(entry)
    return result


def _belongs_to_related(source, target, features):
    current = source
    while current is not None:
        if current == target:
            return True
        current = features[current]["parent"]
    current = target
    while current is not None:
        if current == source:
            return True
        current = features[current]["parent"]
    return False


def _add_use(uses, source, target, kind, features):
    if source == target or _belongs_to_related(source, target, features):
        return
    uses[(source, target)].add(kind)


def build_map(repository):
    """Return the complete machine-readable map, raising ValueError on invalid input."""
    root = require_repository_root(repository)
    paths = sorted(tracked_paths(root))
    features, order, errors = load_features(root, paths)
    if errors:
        raise ValueError("\n".join(errors))
    subrepositories = discover_subrepositories(root)
    if subrepositories:
        raise ValueError("还不支持子仓：" + ", ".join(subrepositories))

    unsupported = sorted(path for path in paths if Path(path).suffix.lower() in UNSUPPORTED_CODE_SUFFIXES)
    if unsupported:
        raise ValueError("已知是代码但不支持统计的文件：" + ", ".join(unsupported))

    categories = {path: _file_category(path) for path in paths}
    source_texts = {path: _source_text(root, path) for path in paths}
    counts = {path: count_file(path, categories[path], source_texts[path] or "") for path in paths}
    python_paths = [path for path in paths if Path(path).suffix.lower() == ".py"]
    imports = {}
    for path in paths:
        if categories[path] in ("code", "tests") and source_texts[path] is not None:
            imports[path] = imports_for_file(path, source_texts[path], python_paths)
        else:
            imports[path] = set()

    entries = _entry_files(features, order, paths)
    owners, groups = assign_files(features, order, paths, categories, entries, imports)
    if len(owners) != len(paths):
        missing = sorted(set(paths) - owners.keys())
        raise ValueError("文件没有归到功能：" + ", ".join(missing))

    lines = {feature_id: {name: 0 for name in LINE_NAMES} for feature_id in order}
    files = {feature_id: {group: [] for group in FILE_GROUPS} for feature_id in order}
    for path in paths:
        owner = owners[path]
        files[owner][groups[path]].append(path)
        for name in LINE_NAMES:
            lines[owner][name] += getattr(counts[path], name)

    children = {feature_id: [] for feature_id in order}
    for feature_id in order:
        parent = features[feature_id]["parent"]
        if parent is not None:
            children[parent].append(feature_id)

    totals = {}

    def total(feature_id):
        result = dict(lines[feature_id])
        for child in children[feature_id]:
            for name, value in total(child).items():
                result[name] += value
        totals[feature_id] = result
        return result

    root_id = order[0]
    total(root_id)

    directories = {
        feature_id: features[feature_id]["directory"]
        for feature_id in order if features[feature_id]["directory"] is not None
    }
    directory_for = {}
    for feature_id in order:
        directory_for[feature_id] = feature_id if features[feature_id]["directory"] is not None else next(
            ancestor for ancestor in _parent_chain(feature_id, features)
            if features[ancestor]["directory"] is not None
        )

    uses = defaultdict(set)
    for source_path in paths:
        if categories[source_path] not in ("code", "docs"):
            continue
        source_owner = owners[source_path]
        for target_path in imports[source_path]:
            if target_path in owners:
                _add_use(uses, source_owner, owners[target_path], "import", features)
        source = source_texts[source_path]
        if source is None:
            continue
        for target_path in file_mentions(source_path, source, paths):
            if target_path in owners:
                _add_use(uses, source_owner, owners[target_path], "mention", features)
        own_directory = directory_for[source_owner]
        for target_feature in directory_mentions(source, directories, own_directory):
            _add_use(uses, source_owner, target_feature, "mention", features)

    index = {feature_id: position for position, feature_id in enumerate(order)}
    result_features = {}
    marker_counts = {name: 0 for name in MARKER_NAMES}
    for feature_id in order:
        value = features[feature_id]
        directory = value["directory"]
        directory_name = root.name if directory == "" else Path(directory).name if directory is not None else None
        markers = []
        if value["why"]["source"] == "起草":
            markers.append("等你拍板")
        if directory is None and not children[feature_id] and not value["entries"]:
            markers.append("还没实现")
        if directory is not None and children[feature_id] and any(
            groups[path] == "whole" and owners[path] == feature_id
            and categories[path] == "code" and counts[path].code > 0
            for path in paths
        ):
            markers.append("没归到功能的代码")
        for marker in markers:
            marker_counts[marker] += 1
        feature_uses = []
        for (source_id, target_id), kinds in uses.items():
            if source_id != feature_id:
                continue
            feature_uses.append({"feature": target_id,
                                 "by": "import" if "import" in kinds else "mention"})
        feature_uses.sort(key=lambda item: index[item["feature"]])
        result_features[feature_id] = {
            "title": value["title"],
            "directory_name": directory_name,
            "parent": value["parent"],
            "children": children[feature_id],
            "what": value["what"],
            "why": value["why"],
            "lines": {"own": lines[feature_id], "total": totals[feature_id]},
            "files": files[feature_id],
            "uses": feature_uses,
            "markers": markers,
        }

    return {"root": root_id, "features": result_features, "markers": marker_counts}


def _parent_chain(feature_id, features):
    current = feature_id
    while current is not None:
        yield current
        current = features[current]["parent"]
