"""Derive graph targets that are used by more than one distinct node."""


def shared_targets(model):
    """Return shared targets with their source nodes and relationship titles."""
    users_by_target = {}
    for relation in model["relations"].values():
        source = relation["source"]["model"]
        target = relation["target"]["model"]
        if target not in users_by_target:
            users_by_target[target] = []
        users_by_target[target].append({"node": source, "title": relation["title"]})

    results = []
    for target in sorted(users_by_target):
        users = sorted(users_by_target[target], key=lambda item: (item["node"], item["title"]))
        if len({user["node"] for user in users}) > 1:
            results.append({"target": target, "users": users})
    return results
