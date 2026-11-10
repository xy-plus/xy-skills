"""Find Python imports and written path or directory mentions in repository files."""
import ast
from pathlib import Path
import re


def _module_path(path):
    if not path.endswith(".py"):
        return None
    value = path[:-3]
    if value.endswith("/__init__"):
        value = value[:-9]
    return value


def _match_module(name, python_paths):
    if not name:
        return None
    suffix = name.replace(".", "/")
    matches = [path for path in python_paths
               if (module := _module_path(path)) is not None
               and (module == suffix or module.endswith("/" + suffix))]
    return matches[0] if len(matches) == 1 else None


def _relative_module(path, level, module):
    parts = Path(path).parent.parts
    climb = max(level - 1, 0)
    if climb > len(parts):
        return None
    base = parts[:len(parts) - climb] if climb else parts
    pieces = [*base]
    if module:
        pieces.extend(module.split("."))
    return ".".join(pieces)


def _import_targets(path, source, python_paths):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    result = set()
    for item in ast.walk(tree):
        if isinstance(item, ast.Import):
            for alias in item.names:
                target = _match_module(alias.name, python_paths)
                if target is not None:
                    result.add(target)
        elif isinstance(item, ast.ImportFrom):
            base = _relative_module(path, item.level, item.module) if item.level else item.module
            if base is None:
                continue
            if item.module is None and item.level:
                groups = [[f"{base}.{alias.name}" if base else alias.name] for alias in item.names]
            elif item.names and not (len(item.names) == 1 and item.names[0].name == "*"):
                groups = [[f"{base}.{alias.name}", base] for alias in item.names]
            else:
                groups = [[base]]
            for candidates in groups:
                for name in candidates:
                    target = _match_module(name, python_paths)
                    if target is not None:
                        result.add(target)
                        break
    return result


def imports_for_file(path, source, python_paths):
    """Return uniquely resolved tracked Python targets imported by a file."""
    if Path(path).suffix.lower() != ".py":
        return set()
    return _import_targets(path, source, python_paths)


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


def directory_mentions(source, directory_features, own_directory_feature):
    """Return other directory features named as whole words in source text."""
    result = set()
    directory_names = {
        feature_id: Path(directory).name if directory else ""
        for feature_id, directory in directory_features.items()
    }
    name_counts = {}
    for name in directory_names.values():
        if name:
            name_counts[name] = name_counts.get(name, 0) + 1

    for feature_id, directory in directory_features.items():
        if feature_id == own_directory_feature:
            continue
        name = directory_names[feature_id]
        if not name or name_counts[name] != 1:
            continue
        pattern = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])")
        if pattern.search(source):
            result.add(feature_id)
    return result
