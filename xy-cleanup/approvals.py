import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


REPOSITORY = Path(__file__).resolve().parents[1]
APPROVED_RELATIVE_PATH = "repo-map/approved.json"
APPROVED_VALUE = re.compile(r"^[0-9a-f]{64}$")
sys.path.insert(0, str(REPOSITORY / "xy-repo-map"))
sys.path.insert(0, str(REPOSITORY / "xy-handoff"))
from repo_map_lib.features import load_features  # noqa: E402
from repo_map_lib.git_repository import ArgumentError, tracked_paths  # noqa: E402
from repo_map_lib.map_data import build_map  # noqa: E402
from check_handoff import object_without_duplicate_keys  # noqa: E402


EXCLUDED_PATHS = {"repo-map/features.json", APPROVED_RELATIVE_PATH}


def parser():
    result = argparse.ArgumentParser(description="按认可记录规划功能清扫审查")
    commands = result.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="列出本次要审的功能")
    plan.add_argument("--repo", required=True, help="仓根的绝对路径")
    plan.add_argument("--scope", required=True, nargs="+", help="功能 id 列表，或单独写 全部")
    plan.add_argument("--review", required=True, choices=("变过的", "全部"),
                      help="筛选变过的功能，或重审全部功能")

    record = commands.add_parser("record", help="记录范围内功能的认可指纹")
    record.add_argument("--repo", required=True, help="仓根的绝对路径")
    record.add_argument("--scope", required=True, nargs="+", help="功能 id 列表，或单独写 全部")
    return result


def _scope_features(data, scope):
    features = data["features"]
    selected = list(features) if scope == ["全部"] else scope
    result = []

    def visit(feature_id):
        result.append(feature_id)
        for child in features[feature_id]["children"]:
            visit(child)

    for feature_id in selected:
        visit(feature_id)
    return list(dict.fromkeys(result))


def validate_scope(data, scope):
    if "全部" in scope and scope != ["全部"]:
        raise ArgumentError("--scope 使用 全部 时不能再列功能 id")
    if scope != ["全部"] and len(scope) != len(set(scope)):
        raise ArgumentError("--scope 不能重复列出功能 id")
    unknown = [feature_id for feature_id in scope
               if feature_id not in data["features"] and feature_id != "全部"]
    if unknown:
        raise ArgumentError("--scope 中有未知功能 id：" + ", ".join(unknown))


def _require_approval_path_in_repository(repository, path):
    message = f"认可记录必须是仓库 repo-map 目录下的普通文件：{path}"
    expected_directory = os.path.join(os.path.realpath(repository), "repo-map")
    if os.path.realpath(path.parent) != expected_directory:
        raise ValueError(message)
    try:
        path_stat = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ValueError(message) from error
    if not stat.S_ISREG(path_stat.st_mode):
        raise ValueError(message)
    return True


def read_approvals(repository, path):
    if not _require_approval_path_in_repository(repository, path):
        return {}
    try:
        records = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_without_duplicate_keys,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"无法读取认可记录 {path}：{error}") from error
    if not isinstance(records, dict):
        raise ValueError(f"认可记录必须是对象：{path}")
    for feature_id, fingerprint in records.items():
        if not isinstance(fingerprint, str):
            raise ValueError(f"认可记录 {feature_id} 的指纹必须是字符串")
        if APPROVED_VALUE.fullmatch(fingerprint) is None:
            raise ValueError(f"认可记录 {feature_id} 的指纹必须是 64 位小写十六进制")
    return records


def feature_values(repository):
    features, order, errors = load_features(repository, tracked_paths(repository))
    if errors:
        raise ValueError("\n".join(errors))
    return features, order


def feature_fingerprints(repository, data, values_by_id):
    """Return content fingerprints for every feature in the checked map."""
    result = {}
    for feature_id in values_by_id:
        feature = values_by_id[feature_id]
        entry = {
            key: value for key, value in feature.items()
            if key not in ("children", "parent")
        }
        entry["children"] = data["features"][feature_id]["children"]
        file_hashes = []
        for group_paths in data["features"][feature_id]["files"].values():
            for relative_path in group_paths:
                if relative_path in EXCLUDED_PATHS:
                    continue
                path = Path(repository) / relative_path
                content = (
                    os.readlink(path).encode("utf-8", errors="surrogateescape")
                    if path.is_symlink() else path.read_bytes()
                )
                file_hashes.append([relative_path, hashlib.sha256(content).hexdigest()])
        payload = {
            "entry": entry,
            "files": sorted(file_hashes, key=lambda item: item[0]),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        result[feature_id] = hashlib.sha256(encoded).hexdigest()
    return result


def review_features(data, fingerprints, records, scope, review):
    selected = _scope_features(data, scope)
    changed = []
    for feature_id in selected:
        if review == "全部":
            changed.append((feature_id, "全部重审"))
        elif feature_id not in records:
            changed.append((feature_id, "没有认可记录"))
        elif fingerprints[feature_id] != records[feature_id]:
            changed.append((feature_id, "认可后变过"))
    return changed


def _plan(data, fingerprints, records, scope, review):
    changed = review_features(data, fingerprints, records, scope, review)
    selected = _scope_features(data, scope)
    if not changed:
        return f"范围内没有变过的功能（范围内 {len(selected)} 个）"
    lines = [f"{feature_id}\t{reason}" for feature_id, reason in changed]
    lines.append(f"要审 {len(changed)} 个功能（范围内 {len(selected)} 个）")
    return "\n".join(lines)


def _record(repository, data, feature_values, fingerprints, records, scope):
    approved_path = repository / APPROVED_RELATIVE_PATH
    selected = _scope_features(data, scope)
    drafts = [feature_id for feature_id in selected
              if feature_values[feature_id]["why"]["source"] == "起草"]
    if drafts:
        raise ValueError("拒绝记录：这些功能的 why.source 仍是 起草：" + ", ".join(drafts))

    selected_set = set(selected)
    updated = {
        feature_id: fingerprint
        for feature_id, fingerprint in records.items()
        if feature_id in feature_values and feature_id not in selected_set
    }
    updated.update({feature_id: fingerprints[feature_id] for feature_id in selected})
    content = json.dumps(updated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=approved_path.parent,
        prefix=f".{approved_path.name}.", suffix=".tmp", delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        temporary_file.write(content)
    _require_approval_path_in_repository(repository, approved_path)
    os.replace(temporary_path, approved_path)
    return f"已记录 {len(selected)} 个功能：{approved_path}"


def main(argv):
    options = parser().parse_args(argv[1:])

    try:
        repository = Path(options.repo)
        data = build_map(repository)
    except ArgumentError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(str(error))
        return 1

    try:
        validate_scope(data, options.scope)
    except ArgumentError as error:
        print(str(error), file=sys.stderr)
        return 2

    try:
        records = read_approvals(repository, repository / APPROVED_RELATIVE_PATH)
        values, _ = feature_values(repository)
        selected = _scope_features(data, options.scope)
        selected_values = {feature_id: values[feature_id] for feature_id in selected}
        fingerprints = feature_fingerprints(repository, data, selected_values)
        if options.command == "plan":
            print(_plan(data, fingerprints, records, options.scope, options.review))
        else:
            print(_record(repository, data, values, fingerprints, records, options.scope))
    except (OSError, UnicodeError, ValueError) as error:
        print(str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
