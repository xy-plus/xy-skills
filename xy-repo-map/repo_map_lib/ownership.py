"""Assign tracked files to the features that register or import them."""
from pathlib import Path


def _under(path, directory):
    return directory == "" or path.startswith(directory + "/")


def _home(feature_id, features):
    current = feature_id
    while current is not None:
        if features[current]["directory"] is not None:
            return current
        current = features[current]["parent"]
    return None


def _ancestors(feature_id, features):
    result = []
    current = feature_id
    while current is not None:
        result.append(current)
        current = features[current]["parent"]
    return result


def _is_ancestor(ancestor, feature_id, features):
    return ancestor in _ancestors(feature_id, features)


def _common_parent(feature_ids, features):
    if not feature_ids:
        return None
    shared = set(_ancestors(feature_ids[0], features))
    for feature_id in feature_ids[1:]:
        shared.intersection_update(_ancestors(feature_id, features))
    return next(item for item in _ancestors(feature_ids[0], features) if item in shared)


def _prune_ancestors(feature_ids, features):
    return {feature_id for feature_id in feature_ids
            if not any(feature_id != other and _is_ancestor(feature_id, other, features)
                       for other in feature_ids)}


def _test_code_match(test_path, code_paths):
    name = Path(test_path).name
    candidate = None
    if name.startswith("test_") and name.endswith(".py"):
        candidate = name[5:]
    elif name.startswith("test_") and name.endswith("_test.py"):
        candidate = name[5:-8] + ".py"
    elif name.endswith("_test.py"):
        candidate = name[:-8] + ".py"
    elif name.endswith("_test.rs"):
        candidate = name[:-8] + ".rs"
    if candidate is None:
        return None
    matches = [path for path in code_paths if Path(path).name == candidate]
    return matches[0] if len(matches) == 1 else None


def assign_files(features, order, paths, categories, entries, imports):
    """Return each tracked path's owner and its reason group."""
    directories = [(feature_id, features[feature_id]["directory"]) for feature_id in order
                   if features[feature_id]["directory"] is not None]
    directory_for = {}
    for path in sorted(paths, key=lambda item: categories[item] == "tests"):
        matches = [(feature_id, directory) for feature_id, directory in directories
                   if _under(path, directory)]
        directory_for[path] = max(matches, key=lambda item: len(item[1]), default=(None, None))[0]

    entry_owners = {path: set() for path in paths}
    for feature_id, feature_paths in entries.items():
        for path in feature_paths:
            entry_owners[path].add(feature_id)
    effective_entries = {path: _prune_ancestors(owners, features)
                         for path, owners in entry_owners.items()}

    reached_by = {path: set() for path in paths}
    feature_home = {feature_id: _home(feature_id, features) for feature_id in order}
    for feature_id in order:
        home = feature_home[feature_id]
        pending = [path for path in entries[feature_id] if categories[path] != "tests"]
        seen = set(pending)
        while pending:
            source = pending.pop()
            for target in imports.get(source, set()):
                if target not in directory_for or directory_for[target] != home or target in seen:
                    continue
                blockers = effective_entries[target]
                if any(not (_is_ancestor(feature_id, owner, features)
                            or _is_ancestor(owner, feature_id, features)) for owner in blockers):
                    continue
                seen.add(target)
                pending.append(target)
        for path in seen:
            reached_by[path].add(feature_id)

    owners, groups = {}, {}
    code_paths = [path for path in paths if categories[path] == "code"]
    for path in sorted(paths, key=lambda item: categories[item] == "tests"):
        home = directory_for[path]
        if home is None:
            continue
        if not features[home]["children"]:
            owners[path], groups[path] = home, "directory"
            continue
        registered = effective_entries[path]
        if registered:
            selected = registered
            group = "registered"
        elif categories[path] == "tests":
            matched_code = _test_code_match(path, code_paths)
            if matched_code is not None:
                owners[path], groups[path] = owners[matched_code], "reached"
                continue
            imported_owners = {
                owners[target] for target in imports.get(path, set())
                if target in owners and categories[target] == "code"
            }
            if imported_owners:
                selected = imported_owners
                group = "reached"
            else:
                owners[path], groups[path] = home, "whole"
                continue
        else:
            selected = reached_by[path]
            if selected:
                group = "reached"
            else:
                owners[path], groups[path] = home, "whole"
                continue

        selected = _prune_ancestors(selected, features)
        if len(selected) == 1:
            owners[path] = next(iter(selected))
            groups[path] = group
        else:
            owners[path] = _common_parent(sorted(selected), features)
            groups[path] = "shared"
    return owners, groups
