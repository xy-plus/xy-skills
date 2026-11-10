"""Assign tracked files to the features that register or import them."""
from pathlib import Path

from .features import (
    deepest_directory,
    directory_home,
    feature_chain,
    is_feature_ancestor,
)


def _common_parent(feature_ids, features):
    if not feature_ids:
        return None
    first_chain = list(feature_chain(feature_ids[0], features))
    shared = set(first_chain)
    for feature_id in feature_ids[1:]:
        shared.intersection_update(feature_chain(feature_id, features))
    return next(item for item in first_chain if item in shared)


def _prune_ancestors(feature_ids, features):
    return {feature_id for feature_id in feature_ids
            if not any(is_feature_ancestor(feature_id, other, features)
                       for other in feature_ids)}


def _test_code_match(test_path, code_paths):
    name = Path(test_path).name
    candidate = None
    if name.startswith("test_") and name.endswith(".py"):
        candidate = name[5:]
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
    directory_by_path = {
        path: deepest_directory(path, features, order)
        for path in paths
    }

    entry_owners = {path: set() for path in paths}
    for feature_id, feature_paths in entries.items():
        for path in feature_paths:
            entry_owners[path].add(feature_id)
    effective_entries = {path: _prune_ancestors(owners, features)
                         for path, owners in entry_owners.items()}

    reached_by = {path: set() for path in paths}
    feature_home = {feature_id: directory_home(feature_id, features) for feature_id in order}
    for feature_id in order:
        home = feature_home[feature_id]
        pending = [path for path in entries[feature_id] if categories[path] != "tests"]
        seen = set(pending)
        while pending:
            source = pending.pop()
            for target in imports.get(source, set()):
                if target not in directory_by_path or directory_by_path[target] != home or target in seen:
                    continue
                blockers = effective_entries[target]
                if any(not (feature_id == owner
                            or is_feature_ancestor(feature_id, owner, features)
                            or is_feature_ancestor(owner, feature_id, features)) for owner in blockers):
                    continue
                seen.add(target)
                pending.append(target)
        for path in seen:
            reached_by[path].add(feature_id)

    owners, groups = {}, {}
    for path in sorted(paths, key=lambda item: categories[item] == "tests"):
        home = directory_by_path[path]
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
            imported_code = {
                target for target in imports.get(path, set())
                if target in owners and categories[target] == "code" and directory_by_path[target] == home
            }
            matched_code = _test_code_match(path, imported_code)
            if matched_code is not None:
                owners[path], groups[path] = owners[matched_code], "reached"
                continue
            imported_owners = {owners[target] for target in imported_code}
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
