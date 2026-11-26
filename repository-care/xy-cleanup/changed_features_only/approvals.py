import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_CARE = REPOSITORY_ROOT / "repository-care"
XY_CLEANUP_DIRECTORY = REPOSITORY_CARE / "xy-cleanup"
DECISION_PAGE_DIRECTORY = XY_CLEANUP_DIRECTORY / "decision_page"
APPROVED_RELATIVE_PATH = "repo-map/approved.json"
FEATURES_RELATIVE_PATH = "repo-map/features.json"
SKIP_RELATIVE_PATH = "repo-map/skip.json"
APPROVED_VALUE = re.compile(r"^[0-9a-f]{40}$")
REASK_OPTION = "留，下次还问我"
EXCLUDED_PATHS = {FEATURES_RELATIVE_PATH, APPROVED_RELATIVE_PATH, SKIP_RELATIVE_PATH}
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_CARE / "xy-repo-map"))
sys.path.insert(0, str(REPOSITORY_CARE))
sys.path.insert(0, str(DECISION_PAGE_DIRECTORY))
from cli_errors import report_cli_argument_error, report_cli_input_error  # noqa: E402
from decision_page import validate_answers, validate_decisions  # noqa: E402
from json_validation import object_without_duplicate_keys  # noqa: E402
from repo_map_lib.validate_feature_list.features import (  # noqa: E402
    _safe_relative_path,
    load_features,
)
from repo_map_lib.git_repository import (  # noqa: E402
    ArgumentError,
    primary_checkout_name,
    require_repository_root,
    run_git,
    tracked_paths,
)
from repo_map_lib.map_data import build_map  # noqa: E402
from repo_map_lib.skip_paths.skip_paths import read_skip_paths  # noqa: E402


def parser():
    result = argparse.ArgumentParser(description="按认可提交规划功能清扫审查")
    commands = result.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="列出本次要审的功能")
    plan.add_argument("--repo", required=True, help="仓根的绝对路径")
    plan.add_argument(
        "--scope", required=True, nargs="+", help="功能 id 列表，或单独写 全部"
    )
    plan.add_argument(
        "--review",
        required=True,
        choices=("变过的", "全部"),
        help="筛选变过的功能，或重审全部功能",
    )

    record = commands.add_parser("record", help="记录决定清单中已回答功能的认可提交")
    record.add_argument("--repo", required=True, help="隔离工作树的仓根绝对路径")
    record.add_argument("--decisions", required=True, help="本次生成的决定清单 JSON")
    record.add_argument("--answers", required=True, help="这份决定清单对应的回答 JSON")
    return result


def _git_output(repository, *arguments):
    result = run_git(repository, *arguments, check=False)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git 命令失败"
        raise ValueError(message)
    return result.stdout


def _head_commit(repository):
    return _git_output(repository, "rev-parse", "--verify", "HEAD").strip()


def _require_clean_worktree(repository):
    changes = _git_output(
        repository,
        "status",
        "--porcelain",
        "--untracked-files=all",
    )
    if changes:
        raise ValueError("工作树必须干净；请先提交或清理所有改动")


def _require_tracked_worktree_clean(repository):
    changes = _git_output(
        repository,
        "status",
        "--porcelain",
        "--untracked-files=no",
    )
    if changes:
        raise ValueError("工作树必须干净；请先提交或清理所有改动")


def _commit_exists(repository, commit):
    if APPROVED_VALUE.fullmatch(commit) is None:
        return False
    result = run_git(
        repository,
        "cat-file",
        "-e",
        f"{commit}^{{commit}}",
        check=False,
    )
    return result.returncode == 0


def _is_ancestor(repository, ancestor, descendant):
    result = run_git(
        repository,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    message = result.stderr.strip() or "无法比较 Git 提交"
    raise ValueError(message)


def _require_approval_path_in_repository(repository, path):
    expected_directory = os.path.join(os.path.realpath(repository), "repo-map")
    message = f"认可记录必须是仓库 repo-map 目录下的普通文件：{path}"
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


def _read_commit_file(repository, commit, relative_path):
    listing = _git_output(repository, "ls-tree", "-z", commit, "--", relative_path)
    if not listing:
        return None
    return _git_output(repository, "show", f"{commit}:{relative_path}")


def _read_json_text(text, label):
    try:
        return json.loads(text, object_pairs_hook=object_without_duplicate_keys)
    except (UnicodeError, ValueError) as error:
        raise ValueError(f"{label}不是合法 JSON：{error}") from error


def _read_committed_approvals(repository, head):
    path = Path(repository) / APPROVED_RELATIVE_PATH
    _require_approval_path_in_repository(repository, path)
    content = _read_commit_file(repository, head, APPROVED_RELATIVE_PATH)
    if content is None:
        return {}
    records = _read_json_text(content, "HEAD 中的认可记录")
    if not isinstance(records, dict):
        raise ValueError("HEAD 中的认可记录必须是对象")
    for feature_id, commit in records.items():
        if not isinstance(commit, str):
            raise ValueError(f"认可记录 {feature_id} 的提交号必须是字符串")
        if APPROVED_VALUE.fullmatch(commit) is None:
            raise ValueError(f"认可记录 {feature_id} 必须是 40 位小写完整提交号")
        if not _commit_exists(repository, commit):
            raise ValueError(f"认可记录 {feature_id} 的提交不存在：{commit}")
        if not _is_ancestor(repository, commit, head):
            raise ValueError(f"认可记录 {feature_id} 的提交不是 HEAD 的祖先：{commit}")
    return records


def feature_values(repository):
    values, order, errors = load_features(repository, tracked_paths(repository))
    if errors:
        raise ValueError("\n".join(errors))
    return values, order


def _scope_features(data, scope):
    features = data["features"]
    selected = list(features) if scope == ["全部"] else scope
    result = []

    def visit(feature_id):
        result.append(feature_id)
        for child_id in features[feature_id]["children"]:
            visit(child_id)

    for feature_id in selected:
        visit(feature_id)
    return list(dict.fromkeys(result))


def validate_scope(data, scope, option_name):
    if "全部" in scope and scope != ["全部"]:
        raise ArgumentError(f"{option_name} 使用 全部 时不能再列功能 id")
    if scope != ["全部"] and len(scope) != len(set(scope)):
        raise ArgumentError(f"{option_name} 不能重复列出功能 id")
    unknown = [
        feature_id
        for feature_id in scope
        if feature_id not in data["features"] and feature_id != "全部"
    ]
    if unknown:
        raise ArgumentError(f"{option_name} 中有未知功能 id：" + ", ".join(unknown))


def _feature_snapshot(repository, commit):
    content = _read_commit_file(repository, commit, FEATURES_RELATIVE_PATH)
    if content is None:
        raise ValueError(f"{commit} 中没有 {FEATURES_RELATIVE_PATH}")
    document = _read_json_text(content, f"{commit} 中的功能图")
    if not isinstance(document, dict) or not isinstance(document.get("id"), str):
        raise ValueError(f"{commit} 中的功能图根记录无效")

    features = {}
    order = []

    def visit(value):
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise ValueError(f"{commit} 中的功能图有无效功能记录")
        feature_id = value["id"]
        if feature_id in features:
            raise ValueError(f"{commit} 中的功能图有重复 id：{feature_id}")
        children = value.get("children")
        if not isinstance(children, list):
            raise ValueError(f"{commit} 中功能 {feature_id} 的 children 不是数组")
        features[feature_id] = value
        order.append(feature_id)
        for child in children:
            visit(child)

    visit(document)
    return {"root": document["id"], "features": features, "order": order}


def _current_snapshot(data, values):
    return {
        "root": data["root"],
        "features": values,
        "order": list(data["features"]),
    }


def _registration_changed(old_snapshot, current_snapshot, feature_id):
    old_features = old_snapshot["features"]
    current_features = current_snapshot["features"]
    if feature_id not in old_features:
        return True
    old = old_features[feature_id]
    current = current_features[feature_id]
    keys = ("title", "what", "why", "items")
    if any(old.get(key) != current.get(key) for key in keys):
        return True
    if feature_id == current_snapshot["root"]:
        root_keys = ("subrepositories", "external_dependencies")
        if any(old.get(key) != current.get(key) for key in root_keys):
            return True
    old_children = {
        child.get("id") for child in old.get("children", []) if isinstance(child, dict)
    }
    current_children = {
        child.get("id")
        for child in current.get("children", [])
        if isinstance(child, dict)
    }
    return old_children != current_children


def _directory_snapshot(snapshot, feature_id, commit, scope_features):
    missing = [
        current_id
        for current_id in snapshot["order"]
        if not isinstance(snapshot["features"][current_id].get("directory"), str)
    ]
    if missing:
        names = ", ".join(scope_features)
        raise ValueError(
            f"认可记录之后的功能图属于旧格式，分不清改动归谁：{names}；记录提交 {commit}。"
            "要审就对这个范围用 --review 全部 重跑"
        )
    if feature_id not in snapshot["features"]:
        return False
    return True


def _snapshot_owner(path, snapshot):
    matches = [
        feature_id
        for feature_id in snapshot["order"]
        if (
            snapshot["features"][feature_id]["directory"] == ""
            or path.startswith(snapshot["features"][feature_id]["directory"] + "/")
        )
    ]
    return max(
        matches,
        key=lambda feature_id: len(snapshot["features"][feature_id]["directory"]),
    )


def _snapshot_paths(repository, commit):
    listing = _git_output(repository, "ls-tree", "-rz", "--full-tree", commit)
    paths = set()
    for record in listing.split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode = metadata.split(" ", 1)[0]
        if mode != "160000":
            paths.add(path)
    return paths


def _diff_records(repository, old_commit, head):
    output = _git_output(
        repository,
        "diff",
        "-M",
        "--name-status",
        "-z",
        old_commit,
        head,
    )
    fields = output.split("\0")
    changes = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status:
            continue
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise ValueError("Git 改名记录格式不完整")
            changes.append((status, fields[index], fields[index + 1]))
            index += 2
        else:
            if index >= len(fields):
                raise ValueError("Git 文件差异记录格式不完整")
            changes.append((status, fields[index]))
            index += 1
    return changes


def _is_excluded(path, old_skip, current_skip):
    if path in EXCLUDED_PATHS:
        return True
    return any(
        path == skipped or (skipped.endswith("/") and path.startswith(skipped))
        for skipped in old_skip | current_skip
    )


@dataclass
class FileComparison:
    repository: Path
    old_commit: str
    head: str
    old_snapshot: dict
    current_snapshot: dict
    old_skip: set
    current_skip: set


def _record_owner_change(path, old_owner, current_owner, changed, transfers):
    if old_owner == current_owner:
        return
    changed.update((old_owner, current_owner))
    transfers.append((path, old_owner, current_owner))


def _shared_path_owners(context, path):
    return (
        _snapshot_owner(path, context.old_snapshot),
        _snapshot_owner(path, context.current_snapshot),
    )


def _changed_file_owners(context):
    changed = set()
    transfers = []
    old_paths = _snapshot_paths(context.repository, context.old_commit)
    current_paths = _snapshot_paths(context.repository, context.head)

    for path in old_paths & current_paths:
        if _is_excluded(path, context.old_skip, context.current_skip):
            continue
        old_owner, current_owner = _shared_path_owners(context, path)
        _record_owner_change(path, old_owner, current_owner, changed, transfers)

    for change in _diff_records(context.repository, context.old_commit, context.head):
        _record_diff_owners(context, change, changed, transfers)
    return changed, transfers


def _record_diff_owners(context, change, changed, transfers):
    status = change[0]
    if status.startswith("R"):
        _record_rename_owners(context, change, changed, transfers)
    elif status.startswith("A"):
        _record_added_owner(context, change[1], changed)
    elif status.startswith("D"):
        _record_deleted_owner(context, change[1], changed)
    elif status.startswith(("M", "T")):
        _record_modified_owners(context, change[1], changed, transfers)


def _record_rename_owners(context, paths, changed, transfers):
    status, old_path, current_path = paths
    if _is_excluded(old_path, context.old_skip, context.current_skip) or _is_excluded(
        current_path, context.old_skip, context.current_skip
    ):
        return
    old_owner = _snapshot_owner(old_path, context.old_snapshot)
    current_owner = _snapshot_owner(current_path, context.current_snapshot)
    if status != "R100" or old_owner != current_owner:
        changed.update((old_owner, current_owner))
    if old_owner != current_owner:
        transfers.append((f"{old_path} -> {current_path}", old_owner, current_owner))


def _record_added_owner(context, path, changed):
    if not _is_excluded(path, context.old_skip, context.current_skip):
        changed.add(_snapshot_owner(path, context.current_snapshot))


def _record_deleted_owner(context, path, changed):
    if not _is_excluded(path, context.old_skip, context.current_skip):
        changed.add(_snapshot_owner(path, context.old_snapshot))


def _record_modified_owners(context, path, changed, transfers):
    if _is_excluded(path, context.old_skip, context.current_skip):
        return
    old_owner, current_owner = _shared_path_owners(context, path)
    changed.update((old_owner, current_owner))
    if old_owner != current_owner:
        transfers.append((path, old_owner, current_owner))


def _skip_set_from_commit(repository, commit):
    content = _read_commit_file(repository, commit, SKIP_RELATIVE_PATH)
    if content is None:
        raise ValueError(f"{commit} 中没有 {SKIP_RELATIVE_PATH}")
    values = _read_json_text(content, f"{commit} 中的跳过名单")
    if not isinstance(values, list):
        raise ValueError(f"{commit} 中的跳过名单必须是字符串数组")
    tracked = _snapshot_paths(repository, commit)
    parsed = set()
    for index, path in enumerate(values):
        label = f"{commit} 中的 {SKIP_RELATIVE_PATH}[{index}]"
        if not isinstance(path, str) or not _safe_relative_path(path, True):
            raise ValueError(f"{label} 不是安全的仓内相对路径")
        if path in parsed:
            raise ValueError(f"{label} 重复：{path}")
        parsed.add(path)
        exists = (
            any(tracked_path.startswith(path) for tracked_path in tracked)
            if path.endswith("/")
            else path in tracked
        )
        if not exists:
            raise ValueError(f"{label} 路径不存在或没有被跟踪文件：{path}")
    return parsed


def _current_skip_set(repository):
    return set(read_skip_paths(repository, tracked_paths(repository)))


def _out_of_scope_message(feature_ids):
    names = ", ".join(sorted(set(feature_ids)))
    return (
        f"功能图在范围外也改过：{names}。按 xy-cleanup:map-update-scope 要审全图："
        "把范围改成「全部」或把它们加进范围重跑"
    )


@dataclass
class PlanInputs:
    repository: Path
    data: dict
    values: dict
    records: dict
    selected: list
    head: str


def _selected_registration_changes(inputs, current_snapshot):
    changed = set()
    snapshots = {}
    for feature_id in inputs.selected:
        commit = inputs.records.get(feature_id)
        if commit is None:
            continue
        try:
            snapshot = _feature_snapshot(inputs.repository, commit)
        except ValueError as error:
            raise ValueError(
                f"认可记录之后的功能图属于旧格式，分不清改动归谁：{feature_id}；"
                f"记录提交 {commit}（{error}）。要审就对这个范围用 --review 全部 重跑"
            ) from error
        if not _directory_snapshot(snapshot, feature_id, commit, [feature_id]):
            raise ValueError(
                f"认可记录之后的功能图不含功能 {feature_id}：记录提交 {commit}。"
                "要审就对这个范围用 --review 全部 重跑"
            )
        snapshots[feature_id] = snapshot
        if _registration_changed(snapshot, current_snapshot, feature_id):
            changed.add(feature_id)
    return changed, snapshots


def _outside_registration_changes(inputs, selected_set, current_snapshot, snapshots):
    outside_changes = []
    outside = set(inputs.data["features"]) - selected_set
    for feature_id in sorted(outside):
        commit = inputs.records.get(feature_id)
        if commit is None:
            outside_changes.append(feature_id)
            continue
        try:
            snapshot = snapshots.get(feature_id)
            if snapshot is None:
                snapshot = _feature_snapshot(inputs.repository, commit)
            if _registration_changed(snapshot, current_snapshot, feature_id):
                outside_changes.append(feature_id)
        except ValueError:
            outside_changes.append(feature_id)
    return outside_changes


def _check_skip_boundary(inputs, current_skip, selected_set, changed):
    root_id = inputs.data["root"]
    root_commit = inputs.records.get(root_id)
    if root_commit is None:
        return
    old_skip = _skip_set_from_commit(inputs.repository, root_commit)
    if old_skip == current_skip:
        return
    if root_id in selected_set:
        changed.add(root_id)
        return
    raise ValueError(
        "跳过名单改过；功能 root 在审查范围外。"
        "按 xy-cleanup:map-update-scope 要审全图："
        "把范围改成「全部」或把 root 加进范围重跑"
    )


def _changed_file_scope(inputs, snapshots, current_snapshot, current_skip):
    changed = set()
    outside_changes = []
    selected_set = set(inputs.selected)
    for feature_id in inputs.selected:
        commit = inputs.records.get(feature_id)
        if commit is None:
            continue
        old_snapshot = snapshots[feature_id]
        old_skip = _skip_set_from_commit(inputs.repository, commit)
        comparison = FileComparison(
            inputs.repository,
            commit,
            inputs.head,
            old_snapshot,
            current_snapshot,
            old_skip,
            current_skip,
        )
        file_changes, transfers = _changed_file_owners(comparison)
        changed.update(file_changes & selected_set)
        for path, old_owner, current_owner in transfers:
            if old_owner != current_owner and (
                old_owner not in selected_set or current_owner not in selected_set
            ):
                outside_changes.extend(
                    (f"{path}（{old_owner}）", f"{path}（{current_owner}）")
                )
    return changed, outside_changes


def _plan_changes(inputs):
    current_snapshot = _current_snapshot(inputs.data, inputs.values)
    current_skip = _current_skip_set(inputs.repository)
    selected_set = set(inputs.selected)
    changed, snapshots = _selected_registration_changes(inputs, current_snapshot)
    outside_changes = _outside_registration_changes(
        inputs, selected_set, current_snapshot, snapshots
    )
    _check_skip_boundary(inputs, current_skip, selected_set, changed)
    file_changes, file_outside_changes = _changed_file_scope(
        inputs, snapshots, current_snapshot, current_skip
    )
    changed.update(file_changes)
    outside_changes.extend(file_outside_changes)
    if outside_changes:
        raise ValueError(_out_of_scope_message(outside_changes))
    return changed


def plan_data(repository, scope, review):
    """Return the checked map, committed approvals, plan rows, and planning HEAD."""
    root = require_repository_root(repository)
    _require_tracked_worktree_clean(root)
    head = _head_commit(root)
    data = build_map(root)
    validate_scope(data, scope, "--scope")
    records = _read_committed_approvals(root, head)
    values, _order = feature_values(root)
    selected = _scope_features(data, scope)
    approval_commits = {
        feature_id: records.get(feature_id, "无") for feature_id in selected
    }

    if review == "全部":
        planned = [(feature_id, "全部重审") for feature_id in selected]
    else:
        changed = _plan_changes(PlanInputs(root, data, values, records, selected, head))
        planned = []
        for feature_id in selected:
            if feature_id not in records:
                planned.append((feature_id, "没有认可记录"))
            elif feature_id in changed:
                planned.append((feature_id, "认可后改过"))
            elif data["features"][feature_id]["cycle_with"]:
                planned.append((feature_id, "有存量互相引用"))
    return data, values, records, planned, approval_commits, head


def _plan(data, planned, approval_commits, scope):
    selected = _scope_features(data, scope)
    if not planned:
        return f"范围内没有变过的功能（范围内 {len(selected)} 个）"
    lines = [
        f"{feature_id}\t{reason}\t{approval_commits[feature_id]}"
        for feature_id, reason in planned
    ]
    lines.append(f"要审 {len(planned)} 个功能（范围内 {len(selected)} 个）")
    return "\n".join(lines)


def _require_linked_worktree(repository):
    git_directory = Path(
        _git_output(
            repository,
            "rev-parse",
            "--path-format=absolute",
            "--git-dir",
        ).strip()
    ).resolve()
    common_directory = Path(
        _git_output(
            repository,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).strip()
    ).resolve()
    if git_directory == common_directory:
        raise ValueError("record 拒绝主检出；请在隔离工作树中运行")


def _load_input_json(path, label):
    input_path = Path(path)
    try:
        content = input_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"无法读取{label} {input_path}：{error}") from error
    return _read_json_text(content, str(input_path))


def _answered_features(decisions, answer_document):
    try:
        checked_decisions = validate_decisions(decisions)
        if not isinstance(answer_document, dict):
            raise ValueError("回答文件必须是对象")
        if answer_document.get("title") != checked_decisions["title"]:
            raise ValueError("回答文件 title 必须与决定清单 title 一致")
        checked_answers = validate_answers(answer_document, checked_decisions)
    except ValueError as error:
        raise ValueError(f"决定或回答无效：{error}") from error
    feature_ids = [
        item["id"] for group in checked_decisions["groups"] for item in group["items"]
    ]
    return checked_decisions["title"], feature_ids, checked_answers


def _plan_commit_from_title(repository, title, head):
    prefix = f"清扫决定：{primary_checkout_name(repository)} 计划提交 "
    if not isinstance(title, str) or not title.startswith(prefix):
        raise ValueError("决定清单 title 必须写本仓的计划提交")
    commit = title[len(prefix) :]
    if APPROVED_VALUE.fullmatch(commit) is None:
        raise ValueError("决定清单 title 中的计划提交必须是 40 位小写完整提交号")
    if not _commit_exists(repository, commit):
        raise ValueError(f"计划提交不存在：{commit}")
    if not _is_ancestor(repository, commit, head):
        raise ValueError(f"计划提交不是当前 HEAD 的祖先：{commit}")
    return commit


def _first_parent_merges(repository, plan_commit, head):
    merges = []
    commit = head
    while commit != plan_commit:
        fields = _git_output(repository, "rev-list", "--parents", "-n", "1", commit)
        parents = fields.split()
        if len(parents) < 2:
            raise ValueError("计划提交不是当前 HEAD 第一父链上的祖先")
        first_parent = parents[1]
        if len(parents) > 2:
            merges.append((commit, first_parent))
        commit = first_parent
    return merges


def _mainline_changed_features(repository, plan_commit, head, data, values):
    current_snapshot = _current_snapshot(data, values)
    current_skip = _current_skip_set(repository)
    changed = set()
    for merge_commit, first_parent in _first_parent_merges(
        repository, plan_commit, head
    ):
        old_snapshot = _feature_snapshot(repository, first_parent)
        merge_snapshot = _feature_snapshot(repository, merge_commit)
        _directory_snapshot(
            old_snapshot, current_snapshot["root"], first_parent, [current_snapshot["root"]]
        )
        _directory_snapshot(
            merge_snapshot,
            current_snapshot["root"],
            merge_commit,
            [current_snapshot["root"]],
        )
        comparison = FileComparison(
            Path(repository),
            first_parent,
            merge_commit,
            old_snapshot,
            current_snapshot,
            _skip_set_from_commit(repository, first_parent),
            current_skip,
        )
        transfers = []
        for change in _diff_records(repository, first_parent, merge_commit):
            _record_diff_owners(comparison, change, changed, transfers)

        for feature_id in current_snapshot["order"]:
            if (
                feature_id not in old_snapshot["features"]
                or feature_id not in merge_snapshot["features"]
                or _registration_changed(old_snapshot, merge_snapshot, feature_id)
            ):
                changed.add(feature_id)
        if _skip_set_from_commit(repository, first_parent) != _skip_set_from_commit(
            repository, merge_commit
        ):
            changed.add(current_snapshot["root"])
    return changed


def _reject_reused_answers(repository, records, feature_ids, plan_commit):
    reused = []
    for feature_id in feature_ids:
        old_commit = records.get(feature_id)
        if old_commit is not None and (
            old_commit == plan_commit
            or not _is_ancestor(repository, old_commit, plan_commit)
        ):
            reused.append(feature_id)
    if reused:
        raise ValueError("这份回答对应的功能已经记过：" + ", ".join(reused))


def _validate_record_features(
    values,
    records,
    feature_ids,
    answers,
    plan_commit,
    repository,
    mainline_changed,
):
    current_ids = set(values)
    selected = [feature_id for feature_id in feature_ids if feature_id in current_ids]
    reask = [
        feature_id
        for feature_id in selected
        if answers[feature_id]["option"] == REASK_OPTION
    ]
    answer_again = set(reask)
    defer_from_mainline = [
        feature_id
        for feature_id in selected
        if feature_id in mainline_changed and feature_id not in answer_again
    ]
    defer = set(defer_from_mainline)
    to_record = [
        feature_id
        for feature_id in selected
        if feature_id not in answer_again and feature_id not in defer
    ]
    _reject_reused_answers(repository, records, to_record, plan_commit)
    drafts = [
        feature_id
        for feature_id in to_record
        if values[feature_id]["why"]["source"] == "起草"
        or any(item["status"] == "起草" for item in values[feature_id]["items"])
    ]
    if drafts:
        raise ValueError(
            "拒绝记录：这些功能的 why.source 或 items.status 仍是 起草："
            + ", ".join(drafts)
        )
    return to_record, reask, defer_from_mainline


def _write_approvals(
    repository,
    head,
    values,
    records,
    selected,
    remove,
    mainline_deferred,
):
    approved_path = Path(repository) / APPROVED_RELATIVE_PATH
    removed = set(remove)
    updated = {
        feature_id: commit
        for feature_id, commit in records.items()
        if feature_id in values
        and feature_id not in selected
        and feature_id not in removed
    }
    updated.update({feature_id: head for feature_id in selected})
    content = json.dumps(updated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=approved_path.parent,
            prefix=f".{approved_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
        _require_approval_path_in_repository(repository, approved_path)
        os.replace(temporary_path, approved_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    lines = [f"已记录 {len(selected)} 个功能：{approved_path}"]
    lines.extend(
        f"留到下次清扫：{feature_id}（主线带来改动）"
        for feature_id in mainline_deferred
    )
    lines.extend(
        f"留到下次清扫：{feature_id}（用户选择再问）" for feature_id in remove
    )
    return "\n".join(lines)


def record_decisions(repository, decisions_path, answers_path):
    root = require_repository_root(repository)
    _require_linked_worktree(root)
    _require_clean_worktree(root)
    head = _head_commit(root)
    data = build_map(root)
    values, _order = feature_values(root)
    records = _read_committed_approvals(root, head)
    decisions = _load_input_json(decisions_path, "决定清单")
    answers = _load_input_json(answers_path, "回答文件")
    title, feature_ids, checked_answers = _answered_features(decisions, answers)
    plan_commit = _plan_commit_from_title(root, title, head)
    mainline_changed = _mainline_changed_features(
        root, plan_commit, head, data, values
    )
    selected, reask, mainline_deferred = _validate_record_features(
        values,
        records,
        feature_ids,
        checked_answers,
        plan_commit,
        root,
        mainline_changed,
    )
    return _write_approvals(
        root,
        head,
        values,
        records,
        selected,
        reask,
        mainline_deferred,
    )


def main(argv):
    options = parser().parse_args(argv[1:])
    repository = Path(options.repo)

    if options.command == "plan":
        try:
            data, _values, _records, planned, approval_commits, _head = plan_data(
                repository,
                options.scope,
                options.review,
            )
            print(_plan(data, planned, approval_commits, options.scope))
        except ArgumentError as error:
            return report_cli_argument_error(error)
        except (OSError, UnicodeError, ValueError) as error:
            return report_cli_input_error(error)
        return 0

    try:
        print(record_decisions(repository, options.decisions, options.answers))
    except ArgumentError as error:
        return report_cli_argument_error(error)
    except (OSError, UnicodeError, ValueError) as error:
        return report_cli_input_error(error)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
