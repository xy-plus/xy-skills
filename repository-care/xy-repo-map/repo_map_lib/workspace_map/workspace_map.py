import json
from pathlib import Path

from ..map_data import build_map
from ..validate_feature_list.features import is_feature_ancestor


def _namespace_child_features(root_id, features, child_maps):
    feature_repositories = {feature_id: "" for feature_id in features}
    repository_roots = {"": root_id}
    for relative_path, child_map in child_maps:
        child_root_id = f"{relative_path}/{child_map['root']}"
        repository_roots[relative_path] = child_root_id
        for feature_id, feature in child_map["features"].items():
            joined_id = f"{relative_path}/{feature_id}"
            joined = dict(feature)
            joined["parent"] = (
                root_id
                if feature_id == child_map["root"]
                else f"{relative_path}/{feature['parent']}"
            )
            joined["children"] = [
                f"{relative_path}/{child_id}" for child_id in feature["children"]
            ]
            joined["uses"] = [
                {**use, "feature": f"{relative_path}/{use['feature']}"}
                for use in feature["uses"]
            ]
            joined["files"] = [f"{relative_path}/{path}" for path in feature["files"]]
            features[joined_id] = joined
            feature_repositories[joined_id] = relative_path
    return feature_repositories, repository_roots


def _merge_child_totals(root, child_maps):
    for relative_path, child_map in child_maps:
        child_root = child_map["features"][child_map["root"]]
        root["children"].append(f"{relative_path}/{child_map['root']}")
        for name, count in child_root["lines"]["total"].items():
            root["lines"]["total"][name] += count
        root["doc_chars"]["total"] += child_root["doc_chars"]["total"]


def _workspace_directory_names(features, feature_repositories, repository_roots):
    names = {}
    for feature_id, feature in features.items():
        repository_path = feature_repositories[feature_id]
        if feature_id == repository_roots[repository_path] and repository_path:
            name = Path(repository_path).name
        else:
            name = feature["directory_name"]
        names.setdefault(name, []).append((repository_path, feature_id))
    return names


def _resolve_workspace_mentions(features, feature_repositories, repository_roots):
    directory_names = _workspace_directory_names(
        features,
        feature_repositories,
        repository_roots,
    )
    order = {feature_id: index for index, feature_id in enumerate(features)}
    for feature_id, feature in features.items():
        remaining = []
        uses = {use["feature"]: dict(use) for use in feature["uses"]}
        for name in feature["external_uses"]:
            targets = [
                target_id
                for repository_path, target_id in directory_names.get(name, [])
                if repository_path != feature_repositories[feature_id]
            ]
            if len(targets) != 1:
                remaining.append(name)
                continue
            target_id = targets[0]
            if is_feature_ancestor(
                feature_id, target_id, features
            ) or is_feature_ancestor(target_id, feature_id, features):
                continue
            uses.setdefault(target_id, {"feature": target_id, "by": "mention"})
        feature["uses"] = sorted(uses.values(), key=lambda use: order[use["feature"]])
        feature["external_uses"] = remaining


def _compose_maps(main_map, child_maps):
    root_id = main_map["root"]
    features = main_map["features"]
    feature_repositories, repository_roots = _namespace_child_features(
        root_id,
        features,
        child_maps,
    )
    _merge_child_totals(features[root_id], child_maps)
    _resolve_workspace_mentions(features, feature_repositories, repository_roots)
    return {"root": root_id, "features": features}


def build_workspace_map(repository):
    """Build each repository map independently, then join their existing results."""
    main_map = build_map(repository)
    repository_root = Path(repository).expanduser().resolve()
    features_path = repository_root / "repo-map" / "features.json"
    features = json.loads(features_path.read_text(encoding="utf-8"))
    registered = features["subrepositories"]
    if not registered:
        raise ValueError("没有登记子仓，用 render 出本仓的图")

    child_maps = []
    for relative_path in dict.fromkeys(registered):
        child_path = repository_root / relative_path
        if not child_path.exists():
            raise ValueError(
                f"子仓 {relative_path} 不存在；请到子仓所在的检出里运行 render-workspace"
            )
        if not (child_path / "repo-map" / "features.json").is_file():
            raise ValueError(
                f"子仓 {relative_path} 缺少 repo-map/features.json；"
                "请先为它起草一份只有根功能的清单"
            )
        try:
            child_map = build_map(child_path)
        except (OSError, ValueError) as error:
            raise ValueError(
                f"子仓 {relative_path} 的功能图检查失败：{error}"
            ) from error
        child_maps.append((relative_path, child_map))
    return _compose_maps(main_map, child_maps)
