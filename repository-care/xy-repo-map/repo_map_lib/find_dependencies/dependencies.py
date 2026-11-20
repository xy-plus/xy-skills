import ast
from collections import Counter
import re


def _match_module(name, python_paths):
    if not name:
        return None
    suffix = name.replace(".", "/")
    matches = []
    for path in python_paths:
        module = path[:-3]
        if module.endswith("/__init__"):
            module = module[:-9]
        if module == suffix or module.endswith("/" + suffix):
            matches.append(path)
    return matches[0] if len(matches) == 1 else None


def _relative_module(path, level, module):
    parts = path.rsplit("/", 1)[0].split("/") if "/" in path else ()
    if level > len(parts) + 1:
        return None
    return ".".join(
        (*parts[: len(parts) + 1 - level], *(module.split(".") if module else ()))
    )


def _absolute_import_targets(statement, python_paths):
    return {
        target
        for alias in statement.names
        if (target := _match_module(alias.name, python_paths)) is not None
    }


def _from_import_candidates(path, statement):
    base = (
        _relative_module(path, statement.level, statement.module)
        if statement.level
        else statement.module
    )
    if base is None:
        return []
    if statement.module is None and statement.level:
        return [
            [f"{base}.{alias.name}" if base else alias.name]
            for alias in statement.names
        ]
    if statement.names and not (
        len(statement.names) == 1 and statement.names[0].name == "*"
    ):
        return [[f"{base}.{alias.name}", base] for alias in statement.names]
    return [[base]]


def _from_import_targets(path, statement, python_paths):
    result = set()
    for candidates in _from_import_candidates(path, statement):
        for name in candidates:
            target = _match_module(name, python_paths)
            if target is not None:
                result.add(target)
                break
    return result


def _import_targets(path, source, python_paths):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    result = set()
    for statement in ast.walk(tree):
        if isinstance(statement, ast.Import):
            result.update(_absolute_import_targets(statement, python_paths))
        elif isinstance(statement, ast.ImportFrom):
            result.update(_from_import_targets(path, statement, python_paths))
    return result


def file_mentions(path, source, tracked_paths):
    """Return other mentioned paths with explicit directories and clean boundaries."""
    result = set()
    for target in tracked_paths:
        if target == path or "/" not in target:
            continue
        pattern = re.compile(rf"(?<![\w./-]){re.escape(target)}(?![\w./-])")
        if pattern.search(source):
            result.add(target)
    return result


def name_mentions(source, names):
    """Return declared names mentioned as whole words in source text."""
    return {
        name
        for name in names
        if re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])",
            source,
        )
    }


def directory_mentions(
    source, directory_features, own_directory_feature, features_with_files
):
    """Return directly populated features named outside slash-delimited paths."""
    directory_names = {
        feature_id: directory.rsplit("/", 1)[-1] if directory else ""
        for feature_id, directory in directory_features.items()
        if feature_id in features_with_files
    }
    unique_names = {
        name
        for name, count in Counter(directory_names.values()).items()
        if name and count == 1
    }
    mentioned = {
        name
        for name in unique_names
        if re.search(
            rf"(?<![A-Za-z0-9_/-]){re.escape(name)}(?![A-Za-z0-9_/-])",
            source,
        )
    }
    return {
        feature_id
        for feature_id, name in directory_names.items()
        if feature_id != own_directory_feature and name in mentioned
    }
