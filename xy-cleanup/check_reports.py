#!/usr/bin/env python3
"""Validate cleanup reviews for every selected feature, including parent features."""
import argparse
import json
import os
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "xy-repo-map"))
sys.path.insert(0, str(REPOSITORY / "xy-review"))
sys.path.insert(0, str(REPOSITORY / "xy-handoff"))
sys.path.insert(0, str(REPOSITORY / "xy-cleanup"))
from repo_map_lib.git_repository import ArgumentError  # noqa: E402
from repo_map_lib.git_repository import tracked_paths  # noqa: E402
from repo_map_lib.map_data import build_map  # noqa: E402
from approvals import (  # noqa: E402
    APPROVED_RELATIVE_PATH,
    feature_fingerprints,
    feature_values,
    read_approvals,
    review_features,
    validate_scope,
)
from check_handoff import object_without_duplicate_keys  # noqa: E402
from rule_references import basis_is_supported  # noqa: E402


REPORT_KEYS = {"role", "repo", "features"}
FEATURE_KEYS = {
    "feature", "what_true", "needed", "smaller", "why_holds", "todo_items", "findings",
}
FINDING_KEYS = {
    "kind", "target", "problem", "proposal", "changes_behavior", "decision",
    "basis", "evidence", "benefit", "cost",
}
BASIS_KEYS = {"source", "text"}
TODO_ITEM_KEYS = {"lines", "needed", "smaller", "why_holds"}
ROLES = {"主审", "剃刀"}
KINDS = {"删除", "合并", "改名", "挪位置", "拆分", "重构", "修根因", "加约束", "改清单", "其他"}
DECISIONS = {"直接做", "问你"}
TODO_FILENAME = "TODO.md"  # 用户各仓的 TODO 都叫 TODO.md。


def parser():
    result = argparse.ArgumentParser(description="校验功能清扫的主审与剃刀报告")
    result.add_argument("--repo", required=True, help="仓根的绝对路径")
    result.add_argument("--scope", required=True, nargs="+", help="功能 id 列表，或单独写 全部")
    result.add_argument(
        "--review", required=True, choices=("变过的", "全部"),
        help="按认可记录筛选变过的功能，或重审全部功能",
    )
    result.add_argument("--reports", required=True, nargs="+", help="JSON 审查报告路径")
    return result


def _field_errors(value, expected, label):
    if not isinstance(value, dict):
        return [f"{label}必须是对象"]
    problems = [f"{label}缺少字段 {key}" for key in sorted(expected - value.keys())]
    problems.extend(f"{label}有多余字段 {key}" for key in sorted(value.keys() - expected))
    return problems


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_finding(finding, label, tracked_todo_files):
    problems = _field_errors(finding, FINDING_KEYS, label)
    if problems:
        return problems
    for key in ("target", "problem", "proposal", "evidence", "benefit", "cost"):
        if not _nonempty(finding[key]):
            problems.append(f"{label}.{key} 必须是非空字符串")
    kind = finding["kind"]
    if not isinstance(kind, str) or kind not in KINDS:
        problems.append(f"{label}.kind 不是允许的类型：{kind!r}")
    changes_behavior = finding["changes_behavior"]
    if not isinstance(changes_behavior, bool):
        problems.append(f"{label}.changes_behavior 必须是布尔值")
    decision = finding["decision"]
    if not isinstance(decision, str) or decision not in DECISIONS:
        problems.append(f"{label}.decision 只能是 直接做 或 问你")

    if (isinstance(changes_behavior, bool) and isinstance(decision, str)
            and decision in DECISIONS and isinstance(kind, str) and kind in KINDS):
        target = finding["target"]
        target_path, separator, _line = (
            target.rpartition(":") if isinstance(target, str) else ("", "", "")
        )
        target_is_todo = separator and target_path in tracked_todo_files
        if target_is_todo:
            # TODO 文件的改动一律由用户决定，即使删除或合并不改变行为。
            if decision != "问你":
                problems.append(
                    f"{label}.decision 目标在 TODO 文件 {finding['target']} 时必须是 问你"
                )
        else:
            must_do_directly = kind in {"删除", "合并"} and not changes_behavior
            if (decision == "直接做") != must_do_directly:
                problems.append(f"{label}.decision 行为不变的删除或合并必须是 直接做；其余必须是 问你")

    basis = finding["basis"]
    problems.extend(_field_errors(basis, BASIS_KEYS, f"{label}.basis"))
    if isinstance(basis, dict):
        for key in BASIS_KEYS:
            if key in basis and not _nonempty(basis[key]):
                problems.append(f"{label}.basis.{key} 必须是非空字符串")
        if set(basis) == BASIS_KEYS and not basis_is_supported(basis):
            problems.append(f"{label}.basis 必须逐字引用有效的 xy-review 规范条款")
    return problems


def _read_file_lines(file_path):
    """Read lines using the same byte and symlink rules as file:line targets."""
    try:
        if file_path.is_symlink():
            return os.readlink(file_path).splitlines()
        return file_path.read_bytes().splitlines()
    except OSError:
        return None


def _valid_target(target, data, repository, tracked_files):
    if target in data["features"]:
        return True
    path, separator, line_text = target.rpartition(":")
    if (not separator or path not in tracked_files
            or not line_text.isascii() or not line_text.isdecimal()):
        return False
    try:
        line_number = int(line_text)
    except ValueError:
        return False
    if line_number < 1:
        return False
    file_path = Path(repository) / path
    lines = _read_file_lines(file_path)
    if lines is None:
        return False
    return line_number <= len(lines)


def _line_is_nonempty(line):
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    return bool(line.strip())


def _validate_todo_items(items, label, repository, direct_todo_files):
    problems = []
    if not isinstance(items, list):
        return [f"{label} 必须是数组"]

    lines_by_file = {}
    occupied_by_file = {}
    for item_index, item in enumerate(items):
        item_label = f"{label}[{item_index}]"
        item_problems = _field_errors(item, TODO_ITEM_KEYS, item_label)
        problems.extend(item_problems)
        if item_problems or not isinstance(item, dict):
            continue
        for key in TODO_ITEM_KEYS:
            if not _nonempty(item[key]):
                problems.append(f"{item_label}.{key} 必须是非空字符串")
        line_spec = item["lines"]
        if not _nonempty(line_spec):
            continue

        path, separator, range_text = line_spec.rpartition(":")
        range_parts = range_text.split("-") if separator else []
        if (not separator or len(range_parts) != 2
                or any(not part.isascii() or not part.isdecimal() for part in range_parts)):
            problems.append(
                f"{item_label}.lines 必须写成 路径:起始行-结束行：{line_spec}"
            )
            continue
        try:
            start_line, end_line = (int(part) for part in range_parts)
        except ValueError:
            problems.append(
                f"{item_label}.lines 行号无法解析：{line_spec}"
            )
            continue
        if path not in direct_todo_files:
            problems.append(
                f"{item_label}.lines 文件不是本功能直属的 TODO 文件：{path}:{start_line}-{end_line}"
            )
            continue

        if path not in lines_by_file:
            lines_by_file[path] = _read_file_lines(Path(repository) / path)
        file_lines = lines_by_file[path]
        if file_lines is None:
            problems.append(
                f"{item_label}.lines 无法读取 TODO 文件：{path}:{start_line}-{end_line}"
            )
            continue
        if start_line < 1 or start_line > end_line or end_line > len(file_lines):
            problems.append(
                f"{item_label}.lines 行范围无效：{path}:{start_line}-{end_line}（文件共 {len(file_lines)} 行）"
            )
            continue

        previous_ranges = occupied_by_file.setdefault(path, [])
        overlapping = next(
            ((previous_start, previous_end) for previous_start, previous_end in previous_ranges
             if start_line <= previous_end and previous_start <= end_line),
            None,
        )
        if overlapping is not None:
            problems.append(
                f"{item_label}.lines 与同一文件的行范围重叠：{path}:{start_line}-{end_line}"
            )
        previous_ranges.append((start_line, end_line))

    for path in sorted(direct_todo_files):
        if path not in lines_by_file:
            lines_by_file[path] = _read_file_lines(Path(repository) / path)
        file_lines = lines_by_file[path]
        if file_lines is None:
            problems.append(f"{label} 无法读取直属 TODO 文件：{path}")
            continue
        covered_lines = {
            line_number
            for start_line, end_line in occupied_by_file.get(path, [])
            for line_number in range(start_line, end_line + 1)
        }
        for line_number, line in enumerate(file_lines, start=1):
            if _line_is_nonempty(line) and line_number not in covered_lines:
                problems.append(
                    f"{label} 未覆盖直属 TODO 的非空行：{path}:{line_number}"
                )
    return problems


def validate_reports(data, repository, expected_features, report_paths, tracked_files):
    """Return all structural, coverage, decision, and rule-quote problems."""
    problems = []
    counts = {role: {} for role in ROLES}
    tracked_todo_files = {
        path for path in tracked_files if Path(path).name == TODO_FILENAME
    }
    for report_path in report_paths:
        try:
            document = json.loads(
                Path(report_path).read_text(encoding="utf-8"),
                object_pairs_hook=object_without_duplicate_keys,
            )
        except (OSError, UnicodeError, ValueError) as error:
            problems.append(f"无法读取报告 {report_path}：{error}")
            continue
        label = str(report_path)
        problems.extend(_field_errors(document, REPORT_KEYS, f"报告 {label}"))
        if not isinstance(document, dict):
            continue
        role = document.get("role")
        if not isinstance(role, str) or role not in ROLES:
            problems.append(f"报告 {label} 的 role 必须是 主审 或 剃刀")
        expected_repository = str(Path(repository).resolve())
        if document.get("repo") != expected_repository:
            problems.append(f"报告 {label} 的 repo 必须等于 {expected_repository}")
        review_features = document.get("features")
        if not isinstance(review_features, list):
            problems.append(f"报告 {label} 的 features 必须是数组")
            continue
        for feature_index, review in enumerate(review_features):
            feature_label = f"报告 {label} 的 features[{feature_index}]"
            problems.extend(_field_errors(review, FEATURE_KEYS, feature_label))
            if not isinstance(review, dict):
                continue
            feature_id = review.get("feature")
            if not isinstance(feature_id, str) or feature_id not in data["features"]:
                problems.append(f"{feature_label}.feature 不在功能清单中：{feature_id!r}")
            elif feature_id not in expected_features:
                problems.append(f"报告功能不在本次审查名单里：{feature_id}")
            elif isinstance(role, str) and role in counts:
                counts[role][feature_id] = counts[role].get(feature_id, 0) + 1
            for key in ("what_true", "needed", "smaller", "why_holds"):
                if not _nonempty(review.get(key)):
                    problems.append(f"{feature_label}.{key} 必须是非空字符串")
            if feature_id in data["features"] and "todo_items" in review:
                direct_todo_files = {
                    path
                    for group_paths in data["features"][feature_id]["files"].values()
                    for path in group_paths
                    if Path(path).name == TODO_FILENAME
                }
                problems.extend(_validate_todo_items(
                    review["todo_items"],
                    f"报告 {label} 的功能 {feature_id}.todo_items",
                    repository,
                    direct_todo_files,
                ))
            elif "todo_items" in review and not isinstance(review["todo_items"], list):
                problems.append(f"{feature_label}.todo_items 必须是数组")
            findings = review.get("findings")
            if not isinstance(findings, list):
                problems.append(f"{feature_label}.findings 必须是数组")
                continue
            for finding_index, finding in enumerate(findings):
                finding_label = f"{feature_label}.findings[{finding_index}]"
                problems.extend(_validate_finding(finding, finding_label, tracked_todo_files))
                if (isinstance(finding, dict) and _nonempty(finding.get("target"))
                        and not _valid_target(
                            finding["target"], data, repository, tracked_files
                        )):
                    problems.append(
                        f"{finding_label}.target 必须是功能 id 或 文件:行号："
                        f"{finding['target']}"
                    )

    for role in sorted(ROLES):
        for feature_id in expected_features:
            count = counts[role].get(feature_id, 0)
            if count == 0:
                problems.append(f"{role}报告缺少功能 {feature_id}")
            elif count > 1:
                problems.append(f"{role}报告重复功能 {feature_id} {count} 次")
    return problems


def main(argv):
    try:
        options = parser().parse_args(argv[1:])
    except SystemExit as error:
        return int(error.code)

    try:
        repository = Path(options.repo)
        data = build_map(repository)
    except ArgumentError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(str(error))
        return 1

    scope = options.scope
    try:
        validate_scope(data, scope)
    except ArgumentError as error:
        print(str(error), file=sys.stderr)
        return 2

    report_paths = [Path(path) for path in options.reports]
    missing = [path for path in report_paths if not path.is_file()]
    if missing:
        print("--reports 必须全部指向已有文件：" + ", ".join(map(str, missing)), file=sys.stderr)
        return 2

    try:
        records = read_approvals(repository, repository / APPROVED_RELATIVE_PATH)
        values, _ = feature_values(repository)
        fingerprints = feature_fingerprints(repository, data, values)
        expected = [
            feature_id
            for feature_id, _reason in review_features(
                data, fingerprints, records, scope, options.review
            )
        ]
    except (OSError, UnicodeError, ValueError) as error:
        print(str(error))
        return 1

    problems = validate_reports(
        data, repository, expected, report_paths, set(tracked_paths(repository))
    )
    if problems:
        print("\n".join(problems))
        return 1
    print("清扫报告检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
