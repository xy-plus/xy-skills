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


def _import_statement_targets(path, statement, python_paths):
    if isinstance(statement, ast.Import):
        return _absolute_import_targets(statement, python_paths)
    if isinstance(statement, ast.ImportFrom):
        return _from_import_targets(path, statement, python_paths)
    return set()


def _import_targets(path, source, python_paths):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    result = set()
    for statement in ast.walk(tree):
        result.update(_import_statement_targets(path, statement, python_paths))
    return result


def _import_targets_with_lines(path, source, python_paths):
    """Return resolved Python imports with the line of each import statement."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {
        (target, statement.lineno)
        for statement in ast.walk(tree)
        for target in _import_statement_targets(path, statement, python_paths)
    }


def find_cycles(edges):
    """Return multi-node strongly connected components and their internal edges."""
    edges = list(edges)
    adjacency, reverse = _cycle_adjacency(edges)
    finish_order = _cycle_finish_order(adjacency)
    components = _cycle_components(reverse, finish_order)
    cycle_edges = _internal_cycle_edges(edges, components)
    return components, cycle_edges


def _cycle_adjacency(edges):
    adjacency = {}
    reverse = {}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set())
        reverse.setdefault(target, set()).add(source)
        reverse.setdefault(source, set())
    return adjacency, reverse


def _cycle_finish_order(adjacency):
    visited = set()
    finish_order = []
    for node in sorted(adjacency):
        if node in visited:
            continue
        stack = [(node, False)]
        while stack:
            current, expanded = stack.pop()
            if expanded:
                finish_order.append(current)
            elif current not in visited:
                visited.add(current)
                stack.append((current, True))
                stack.extend(
                    (target, False)
                    for target in sorted(adjacency[current], reverse=True)
                )
    return finish_order


def _cycle_components(reverse, finish_order):
    visited = set()
    components = []
    for node in reversed(finish_order):
        if node in visited:
            continue
        component = []
        pending = [node]
        visited.add(node)
        while pending:
            current = pending.pop()
            component.append(current)
            for source in sorted(reverse[current], reverse=True):
                if source not in visited:
                    visited.add(source)
                    pending.append(source)
        if len(component) > 1:
            components.append(tuple(sorted(component)))
    return sorted(components)


def _internal_cycle_edges(edges, components):
    component_by_node = {
        node: component for component in components for node in component
    }
    return {
        (source, target)
        for source, target in edges
        if source in component_by_node
        and component_by_node[source] == component_by_node.get(target)
    }


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
