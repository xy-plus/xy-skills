from dataclasses import dataclass
from pathlib import Path

from .constants import (
    CODE_SUFFIXES,
    LINE_NAMES,
    TEST_DIRECTORY_NAMES,
    UNSUPPORTED_CODE_SUFFIXES,
)
from .find_dependencies.dependencies import (
    _import_targets,
    directory_mentions,
    file_mentions,
    name_mentions,
)
from .validate_feature_list.features import is_feature_ancestor, load_features
from .git_repository import (
    primary_checkout_name,
    require_repository_root,
    subrepository_errors,
    tracked_paths,
)
from .count_lines.metrics import count_file
from .assign_files.ownership import assign_files
from .skip_paths.skip_paths import is_skipped_path, read_skip_paths


CODE_CANDIDATE_SUFFIXES = set(CODE_SUFFIXES) | {".html"} | UNSUPPORTED_CODE_SUFFIXES


def _file_category(path):
    suffix = Path(path).suffix.lower()
    name = Path(path).name
    in_test_directory = any(
        part in TEST_DIRECTORY_NAMES for part in Path(path).parts[:-1]
    )
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
    # 用户允许 count-lines:non-text-inputs 只读原始字节识别 NUL；命中后不解码、计数或建关系。
    if b"\0" in content:
        return "other", ""
    return category, content.decode("utf-8", errors="replace")


def file_category_with_skip(repository, path, skip_paths):
    """Classify a path using a skip list that was already validated."""
    if is_skipped_path(path, skip_paths):
        return "other"
    return _read_source(repository, path, _file_category(path))[0]


def _add_use(uses, source, target, kind, features):
    if (
        source == target
        or is_feature_ancestor(source, target, features)
        or is_feature_ancestor(target, source, features)
    ):
        return
    uses.setdefault((source, target), set()).add(kind)


def _load_inputs(root):
    paths = sorted(tracked_paths(root))
    features, order, errors = load_features(root, paths)
    if errors:
        raise ValueError("\n".join(errors))
    skip_paths = read_skip_paths(root, paths)
    errors = subrepository_errors(root, features[order[0]]["subrepositories"])
    if errors:
        raise ValueError("\n".join(errors))
    return paths, features, order, skip_paths


def _read_sources(root, paths, skip_paths):
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
        path
        for path in paths
        if Path(path).suffix.lower() in UNSUPPORTED_CODE_SUFFIXES
        and categories[path] != "other"
    )
    if unsupported:
        raise ValueError("已知是代码但不支持统计的文件：" + ", ".join(unsupported))
    return categories, source_texts


def _external_references(features, order, paths, categories, source_texts):
    external_dependencies = features[order[0]]["external_dependencies"]
    external_directory_names = {
        Path(features[feature_id]["directory"]).name
        for feature_id in order
        if features[feature_id]["directory"] != ""
    }
    external_mentions = {}
    mentioned_dependencies = set()
    for path in paths:
        if categories[path] not in ("code", "docs"):
            continue
        names = name_mentions(source_texts[path], external_dependencies)
        external_mentions[path] = names
        mentioned_dependencies.update(names)

    errors = []
    for name in dict.fromkeys(external_dependencies):
        if name not in mentioned_dependencies:
            errors.append(f"外部依赖没有被任何代码或文档提到：{name}")
        if name in external_directory_names:
            errors.append(f"外部依赖不能与仓内目录功能同名：{name}")
    if errors:
        raise ValueError("\n".join(errors))
    return external_mentions


def _count_sources(paths, categories, source_texts):
    return {
        path: count_file(path, categories[path], source_texts[path]) for path in paths
    }


def _python_imports(paths, categories, source_texts):
    python_paths = [path for path in paths if Path(path).suffix.lower() == ".py"]
    imports = {}
    for path in paths:
        if Path(path).suffix.lower() == ".py" and categories[path] in ("code", "tests"):
            imports[path] = _import_targets(path, source_texts[path], python_paths)
        else:
            imports[path] = set()
    return imports


def _aggregate_feature_metrics(features, order, paths, owners, counts):
    lines = {feature_id: {name: 0 for name in LINE_NAMES} for feature_id in order}
    doc_chars = {feature_id: 0 for feature_id in order}
    files = {feature_id: [] for feature_id in order}
    for path in paths:
        owner = owners[path]
        files[owner].append(path)
        doc_chars[owner] += counts[path].doc_chars
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

    total(order[0])
    return lines, doc_chars, files, children, totals, total_doc_chars


@dataclass
class UseInputs:
    order: list
    features: dict
    paths: list
    categories: dict
    source_texts: dict
    external_mentions: dict
    imports: dict
    owners: dict
    files: dict


@dataclass
class UseGraph:
    by_source: dict
    external: dict


@dataclass
class UseDerivation:
    inputs: UseInputs
    directories: dict
    features_with_files: set
    uses: dict
    external_uses: dict


def _record_file_uses(context, source_path):
    inputs = context.inputs
    uses = context.uses
    external_uses = context.external_uses
    category = inputs.categories[source_path]
    if category not in ("code", "docs", "tests"):
        return
    source_owner = inputs.owners[source_path]
    external_uses[source_owner].update(inputs.external_mentions.get(source_path, ()))
    for target_path in inputs.imports[source_path]:
        if target_path in inputs.owners:
            _add_use(
                uses,
                source_owner,
                inputs.owners[target_path],
                "import",
                inputs.features,
            )
    if category not in ("code", "docs"):
        return
    source = inputs.source_texts[source_path]
    for target_path in file_mentions(source_path, source, inputs.paths):
        target_owner = inputs.owners.get(target_path)
        if target_owner is not None:
            _add_use(uses, source_owner, target_owner, "mention", inputs.features)
    for target_feature in directory_mentions(
        source,
        context.directories,
        source_owner,
        context.features_with_files,
    ):
        _add_use(uses, source_owner, target_feature, "mention", inputs.features)


def _derive_feature_uses(inputs):
    order = inputs.order
    features = inputs.features
    directories = {
        feature_id: features[feature_id]["directory"] for feature_id in order
    }
    features_with_files = {
        feature_id
        for feature_id, feature_files in inputs.files.items()
        if feature_files
    }
    context = UseDerivation(
        inputs,
        directories,
        features_with_files,
        {},
        {feature_id: set() for feature_id in order},
    )
    for source_path in inputs.paths:
        _record_file_uses(context, source_path)

    index = {feature_id: position for position, feature_id in enumerate(order)}
    uses_by_source = {feature_id: [] for feature_id in order}
    for (source_id, target_id), kinds in context.uses.items():
        uses_by_source[source_id].append(
            {
                "feature": target_id,
                "by": "import" if "import" in kinds else "mention",
            }
        )
    for feature_uses in uses_by_source.values():
        feature_uses.sort(key=lambda item: index[item["feature"]])
    return UseGraph(uses_by_source, context.external_uses)


def _assemble_features(
    root,
    features,
    order,
    metrics,
    use_graph,
):
    lines, doc_chars, files, children, totals, total_doc_chars = metrics
    repository_name = primary_checkout_name(root)
    result = {}
    for feature_id in order:
        value = features[feature_id]
        directory = value["directory"]
        directory_name = repository_name if directory == "" else Path(directory).name
        markers = []
        if value["why"]["source"] == "起草" or any(
            item["status"] == "起草" for item in value["items"]
        ):
            markers.append("等你拍板")
        result[feature_id] = {
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
            "uses": use_graph.by_source[feature_id],
            "external_uses": sorted(use_graph.external[feature_id]),
            "markers": markers,
        }
    return result


def build_map(repository):
    """Return the complete machine-readable map, raising ValueError on invalid input."""
    root = require_repository_root(repository)
    paths, features, order, skip_paths = _load_inputs(root)
    categories, source_texts = _read_sources(root, paths, skip_paths)
    external_mentions = _external_references(
        features,
        order,
        paths,
        categories,
        source_texts,
    )
    counts = _count_sources(paths, categories, source_texts)
    imports = _python_imports(paths, categories, source_texts)
    owners = assign_files(features, order, paths)
    metrics = _aggregate_feature_metrics(features, order, paths, owners, counts)
    lines, doc_chars, files, children, totals, total_doc_chars = metrics
    use_graph = _derive_feature_uses(
        UseInputs(
            order,
            features,
            paths,
            categories,
            source_texts,
            external_mentions,
            imports,
            owners,
            files,
        )
    )
    result_features = _assemble_features(
        root,
        features,
        order,
        metrics,
        use_graph,
    )
    return {"root": order[0], "features": result_features}
