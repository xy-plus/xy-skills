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
from .dependencies import (
    directory_mentions,
    file_mentions,
    imports_for_file,
    name_mentions,
)
from .features import directory_home, is_feature_ancestor, load_features
from .git_repository import (
    primary_checkout_name,
    require_repository_root,
    subrepository_errors,
    tracked_paths,
)
from .metrics import count_file
from .ownership import assign_files
from .skip_paths import is_skipped_path, read_skip_paths


SOURCE_SUFFIXES = set(CODE_SUFFIXES) | {".html"}
CODE_CANDIDATE_SUFFIXES = SOURCE_SUFFIXES | UNSUPPORTED_CODE_SUFFIXES
TEST_FILE_ENDINGS = ("_test.py", "_test.rs", "_tests.rs")


def _is_test_file(path):
    suffix = Path(path).suffix.lower()
    name = Path(path).name
    has_code_suffix = suffix in CODE_CANDIDATE_SUFFIXES
    in_test_directory = any(part in TEST_DIRECTORY_NAMES for part in Path(path).parts[:-1])
    named_test = (
        (name.startswith("test_") and suffix == ".py")
        or name == "conftest.py"
        or name == "tests.rs"
        or name.endswith(TEST_FILE_ENDINGS)
    )
    return has_code_suffix and (in_test_directory or named_test)


def _file_category(path):
    if any(part in ("third_party", "vendor", "node_modules") for part in Path(path).parts):
        return "other"
    if _is_test_file(path):
        return "tests"
    suffix = Path(path).suffix.lower()
    if suffix in CODE_CANDIDATE_SUFFIXES:
        return "code"
    if suffix in (".md", ".rst") and not any(
        part in TEST_DIRECTORY_NAMES for part in Path(path).parts[:-1]
    ):
        return "docs"
    return "other"


def _source_text(repository, path):
    """Read text sources, keeping symlinks and binary files out of text categories."""
    source_path = Path(repository) / path
    if source_path.is_symlink():
        return None
    try:
        content = source_path.read_bytes()
    except OSError as error:
        raise OSError(f"无法读取 Git 跟踪文件 {path}：{error}") from error
    if b"\0" in content:
        return None
    return content.decode("utf-8", errors="replace")


def _read_source(repository, path, category):
    """Return the effective file category and text, without reading other files."""
    if category == "other":
        return "other", ""
    source = _source_text(repository, path)
    if source is None:
        return "other", ""
    return category, source


def file_category(repository, path):
    """Return the effective category used by the repository map for one path."""
    root = Path(repository)
    skip_paths = read_skip_paths(root, tracked_paths(root))
    return file_category_with_skip(root, path, skip_paths)


def file_category_with_skip(repository, path, skip_paths):
    """Classify a path using a skip list that was already validated."""
    if is_skipped_path(path, skip_paths):
        return "other"
    return _read_source(repository, path, _file_category(path))[0]


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


def _add_use(uses, source, target, kind, features):
    if (source == target or is_feature_ancestor(source, target, features)
            or is_feature_ancestor(target, source, features)):
        return
    uses[(source, target)].add(kind)


def build_map(repository):
    """Return the complete machine-readable map, raising ValueError on invalid input."""
    root = require_repository_root(repository)
    paths = sorted(tracked_paths(root))
    features, order, errors = load_features(root, paths)
    if errors:
        raise ValueError("\n".join(errors))
    skip_paths = read_skip_paths(root, paths)
    errors = subrepository_errors(root, features[order[0]]["subrepositories"])
    if errors:
        raise ValueError("\n".join(errors))

    categories = {}
    source_texts = {}
    for path in paths:
        if is_skipped_path(path, skip_paths):
            categories[path], source_texts[path] = "other", ""
            continue
        categories[path], source_texts[path] = _read_source(
            root, path, _file_category(path)
        )

    unsupported = sorted(
        path for path in paths
        if Path(path).suffix.lower() in UNSUPPORTED_CODE_SUFFIXES
        and categories[path] != "other"
    )
    if unsupported:
        raise ValueError("已知是代码但不支持统计的文件：" + ", ".join(unsupported))

    external_dependencies = features[order[0]]["external_dependencies"]
    external_directory_names = {
        Path(features[feature_id]["directory"]).name
        for feature_id in order
        if features[feature_id]["directory"] not in (None, "")
    }
    external_mentions = {}
    mentioned_external_dependencies = set()
    for path in paths:
        if categories[path] not in ("code", "docs"):
            continue
        names = name_mentions(source_texts[path], external_dependencies)
        external_mentions[path] = names
        mentioned_external_dependencies.update(names)

    external_errors = []
    for name in dict.fromkeys(external_dependencies):
        if name not in mentioned_external_dependencies:
            external_errors.append(f"外部依赖没有被任何代码或文档提到：{name}")
        if name in external_directory_names:
            external_errors.append(f"外部依赖不能与仓内目录功能同名：{name}")
    if external_errors:
        raise ValueError("\n".join(external_errors))

    counts = {
        path: count_file(path, categories[path], source_texts[path])
        for path in paths
    }
    python_paths = [path for path in paths if Path(path).suffix.lower() == ".py"]
    imports = {}
    for path in paths:
        if categories[path] in ("code", "tests"):
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
    repository_name = primary_checkout_name(root)

    directories = {
        feature_id: features[feature_id]["directory"]
        for feature_id in order if features[feature_id]["directory"] is not None
    }
    feature_home = {
        feature_id: directory_home(feature_id, features)
        for feature_id in order
    }

    uses = defaultdict(set)
    external_uses = {feature_id: set() for feature_id in order}
    for source_path in paths:
        if categories[source_path] not in ("code", "docs"):
            continue
        source_owner = owners[source_path]
        external_uses[source_owner].update(external_mentions[source_path])
        for target_path in imports[source_path]:
            if target_path in owners:
                _add_use(uses, source_owner, owners[target_path], "import", features)
        source = source_texts[source_path]
        own_directory = feature_home[source_owner]
        # Parent-directory overview files can name child features without depending on them.
        for target_path in file_mentions(source_path, source, paths):
            target_owner = owners.get(target_path)
            if target_owner is not None and not is_feature_ancestor(
                own_directory, feature_home[target_owner], features
            ):
                _add_use(uses, source_owner, target_owner, "mention", features)
        for target_feature in directory_mentions(source, directories, own_directory):
            target_directory = feature_home[target_feature]
            if not is_feature_ancestor(own_directory, target_directory, features):
                _add_use(uses, source_owner, target_feature, "mention", features)

    index = {feature_id: position for position, feature_id in enumerate(order)}
    uses_by_source = {feature_id: [] for feature_id in order}
    for (source_id, target_id), kinds in uses.items():
        uses_by_source[source_id].append({
            "feature": target_id,
            "by": "import" if "import" in kinds else "mention",
        })
    for feature_uses in uses_by_source.values():
        feature_uses.sort(key=lambda item: index[item["feature"]])

    result_features = {}
    marker_counts = {name: 0 for name in MARKER_NAMES}
    for feature_id in order:
        value = features[feature_id]
        directory = value["directory"]
        directory_name = repository_name if directory == "" else Path(directory).name if directory is not None else None
        markers = []
        if value["why"]["source"] == "起草":
            markers.append("等你拍板")
        if directory is None and not children[feature_id] and not value["entries"]:
            markers.append("还没实现")
        if directory is not None and children[feature_id] and any(
            groups[path] == "whole" and owners[path] == feature_id
            and (
                (categories[path] == "code" and counts[path].code > 0)
                or (categories[path] == "tests" and counts[path].tests > 0)
            )
            for path in paths
        ):
            markers.append("没归到功能的代码或测试")
        for marker in markers:
            marker_counts[marker] += 1
        feature_uses = uses_by_source[feature_id]
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
            "external_uses": sorted(external_uses[feature_id]),
            "markers": markers,
        }

    return {"root": root_id, "features": result_features, "markers": marker_counts}
