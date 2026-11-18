from pathlib import Path

from .constants import (
    CODE_SUFFIXES,
    LINE_NAMES,
    MARKER_NAMES,
    TEST_DIRECTORY_NAMES,
    UNSUPPORTED_CODE_SUFFIXES,
)
from .dependencies import (
    directory_mentions,
    file_mentions,
    name_mentions,
    _import_targets,
)
from .features import is_feature_ancestor, load_features
from .git_repository import (
    primary_checkout_name,
    require_repository_root,
    subrepository_errors,
    tracked_paths,
)
from .metrics import count_doc_chars, count_file
from .ownership import assign_files
from .skip_paths import is_skipped_path, read_skip_paths


CODE_CANDIDATE_SUFFIXES = set(CODE_SUFFIXES) | {".html"} | UNSUPPORTED_CODE_SUFFIXES


def _file_category(path):
    if any(part in ("third_party", "vendor", "node_modules") for part in Path(path).parts):
        return "other"
    suffix = Path(path).suffix.lower()
    name = Path(path).name
    in_test_directory = any(part in TEST_DIRECTORY_NAMES for part in Path(path).parts[:-1])
    named_test = (
        (name.startswith("test_") and suffix == ".py")
        or name == "conftest.py"
        or name == "tests.rs"
        or name.endswith(("_test.py", "_test.rs", "_tests.rs"))
    )
    if suffix in CODE_CANDIDATE_SUFFIXES and (in_test_directory or named_test):
        return "tests"
    if suffix in CODE_CANDIDATE_SUFFIXES:
        return "code"
    if suffix in (".md", ".rst") and not any(
        part in TEST_DIRECTORY_NAMES for part in Path(path).parts[:-1]
    ):
        return "docs"
    return "other"


def _read_source(repository, path, category):
    """Return the effective file category and text, without reading other files."""
    if category == "other":
        return "other", ""
    source_path = Path(repository) / path
    if source_path.is_symlink():
        return "other", ""
    try:
        content = source_path.read_bytes()
    except OSError as error:
        raise OSError(f"无法读取 Git 跟踪文件 {path}：{error}") from error
    if b"\0" in content:
        return "other", ""
    return category, content.decode("utf-8", errors="replace")


def file_category_with_skip(repository, path, skip_paths):
    """Classify a path using a skip list that was already validated."""
    if is_skipped_path(path, skip_paths):
        return "other"
    return _read_source(repository, path, _file_category(path))[0]


def _add_use(uses, source, target, kind, features):
    if (source == target or is_feature_ancestor(source, target, features)
            or is_feature_ancestor(target, source, features)):
        return
    uses.setdefault((source, target), set()).add(kind)


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
        if features[feature_id]["directory"] != ""
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
    doc_chars = {feature_id: 0 for feature_id in order}
    python_paths = [path for path in paths if Path(path).suffix.lower() == ".py"]
    imports = {}
    for path in paths:
        if Path(path).suffix.lower() != ".py" or categories[path] != "code":
            imports[path] = set()
        else:
            imports[path] = _import_targets(path, source_texts[path], python_paths)

    owners = assign_files(features, order, paths)

    lines = {feature_id: {name: 0 for name in LINE_NAMES} for feature_id in order}
    files = {feature_id: [] for feature_id in order}
    for path in paths:
        owner = owners[path]
        files[owner].append(path)
        doc_chars[owner] += count_doc_chars(
            path, categories[path], source_texts[path],
        )
        for name in LINE_NAMES:
            lines[owner][name] += getattr(counts[path], name)

    children = {feature_id: [] for feature_id in order}
    for feature_id in order:
        parent = features[feature_id]["parent"]
        if parent is not None:
            children[parent].append(feature_id)

    totals = {}
    total_doc_chars = {}

    def total(feature_id):
        result = dict(lines[feature_id])
        result_doc_chars = doc_chars[feature_id]
        for child in children[feature_id]:
            child_lines = total(child)
            for name, value in child_lines.items():
                result[name] += value
            result_doc_chars += total_doc_chars[child]
        totals[feature_id] = result
        total_doc_chars[feature_id] = result_doc_chars
        return result

    root_id = order[0]
    total(root_id)
    repository_name = primary_checkout_name(root)

    directories = {
        feature_id: features[feature_id]["directory"]
        for feature_id in order
    }

    uses = {}
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
        for target_path in file_mentions(source_path, source, paths):
            target_owner = owners.get(target_path)
            if target_owner is not None:
                _add_use(uses, source_owner, target_owner, "mention", features)
        for target_feature in directory_mentions(source, directories, source_owner):
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
        directory_name = repository_name if directory == "" else Path(directory).name
        markers = []
        if (value["why"]["source"] == "起草"
                or any(item["status"] == "起草" for item in value["items"])):
            markers.append("等你拍板")
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
            "items": value["items"],
            "lines": {"own": lines[feature_id], "total": totals[feature_id]},
            "doc_chars": {
                "own": doc_chars[feature_id],
                "total": total_doc_chars[feature_id],
            },
            "files": files[feature_id],
            "uses": feature_uses,
            "external_uses": sorted(external_uses[feature_id]),
            "markers": markers,
        }

    return {"root": root_id, "features": result_features, "markers": marker_counts}
