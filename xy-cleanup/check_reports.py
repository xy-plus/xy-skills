#!/usr/bin/env python3
"""Validate cleanup reviews for every smallest feature in a requested scope."""
import argparse
import importlib
import json
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "xy-repo-map"))
sys.path.insert(0, str(REPOSITORY / "xy-review"))

git_repository = importlib.import_module("repo_map_lib.git_repository")
map_data = importlib.import_module("repo_map_lib.map_data")
rule_references = importlib.import_module("rule_references")
ArgumentError = git_repository.ArgumentError
build_map = map_data.build_map
basis_is_supported = rule_references.basis_is_supported


REPORT_KEYS = {"role", "repo", "features"}
FEATURE_KEYS = {"feature", "needed", "smaller", "why_holds", "findings"}
FINDING_KEYS = {
    "kind", "target", "problem", "proposal", "changes_behavior", "decision",
    "basis", "evidence", "benefit", "cost",
}
BASIS_KEYS = {"source", "text"}
ROLES = {"主审", "剃刀"}
KINDS = {"删除", "合并", "改名", "挪位置", "拆分", "重构", "修根因", "加约束", "改清单", "其他"}
DECISIONS = {"直接做", "问你"}


def parser():
    result = argparse.ArgumentParser(description="校验功能清扫的主审与剃刀报告")
    result.add_argument("--repo", required=True, help="仓根的绝对路径")
    result.add_argument("--scope", required=True, nargs="+", help="功能 id 列表，或单独写 全部")
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


def _leaf_features(data, scope):
    features = data["features"]
    if scope == ["全部"]:
        selected = list(features)
    else:
        selected = scope
    leaves = []

    def visit(feature_id):
        feature = features[feature_id]
        if feature["children"]:
            for child in feature["children"]:
                visit(child)
        else:
            leaves.append(feature_id)

    for feature_id in selected:
        visit(feature_id)
    return list(dict.fromkeys(leaves))


def _validate_finding(finding, label):
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
        must_do_directly = kind in {"删除", "合并"} and not changes_behavior
        if (decision == "直接做") != must_do_directly:
            problems.append(f"{label}.decision 只有行为不变的删除或合并才能是 直接做")
        if kind == "改清单" and decision != "问你":
            problems.append(f"{label} 的 改清单 必须是 问你")

    basis = finding["basis"]
    problems.extend(_field_errors(basis, BASIS_KEYS, f"{label}.basis"))
    if isinstance(basis, dict):
        for key in BASIS_KEYS:
            if key in basis and not _nonempty(basis[key]):
                problems.append(f"{label}.basis.{key} 必须是非空字符串")
        if set(basis) == BASIS_KEYS and not basis_is_supported(basis):
            problems.append(f"{label}.basis 必须逐字引用有效的 xy-review 规范条款")
    return problems


def validate_reports(data, repository, expected_features, report_paths):
    """Return all structural, coverage, decision, and rule-quote problems."""
    problems = []
    counts = {role: {} for role in ROLES}
    for report_path in report_paths:
        try:
            document = json.loads(Path(report_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            problems.append(f"无法读取报告 {report_path}：{error}")
            continue
        label = str(report_path)
        problems.extend(_field_errors(document, REPORT_KEYS, f"报告 {label}"))
        if not isinstance(document, dict):
            continue
        role = document.get("role")
        if not isinstance(role, str) or role not in ROLES:
            problems.append(f"报告 {label} 的 role 必须是 主审 或 剃刀")
        elif role in counts:
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
                problems.append(f"报告功能超出清扫范围或不是最细功能：{feature_id}")
            elif role in counts:
                counts[role][feature_id] = counts[role].get(feature_id, 0) + 1
            for key in ("needed", "smaller", "why_holds"):
                if not _nonempty(review.get(key)):
                    problems.append(f"{feature_label}.{key} 必须是非空字符串")
            findings = review.get("findings")
            if not isinstance(findings, list):
                problems.append(f"{feature_label}.findings 必须是数组")
                continue
            for finding_index, finding in enumerate(findings):
                problems.extend(_validate_finding(finding, f"{feature_label}.findings[{finding_index}]"))

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
        if not repository.is_absolute():
            raise ArgumentError("--repo 必须是绝对路径")
        data = build_map(repository)
    except ArgumentError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(str(error))
        return 1

    scope = options.scope
    if "全部" in scope and scope != ["全部"]:
        print("--scope 使用 全部 时不能再列功能 id", file=sys.stderr)
        return 2
    if scope != ["全部"] and len(scope) != len(set(scope)):
        print("--scope 不能重复列出功能 id", file=sys.stderr)
        return 2
    unknown = [feature_id for feature_id in scope if feature_id not in data["features"] and feature_id != "全部"]
    if unknown:
        print("--scope 中有未知功能 id：" + ", ".join(unknown), file=sys.stderr)
        return 2

    expected = _leaf_features(data, scope)
    report_paths = [Path(path) for path in options.reports]
    missing = [path for path in report_paths if not path.is_file()]
    if missing:
        print("--reports 必须全部指向已有文件：" + ", ".join(map(str, missing)), file=sys.stderr)
        return 2

    problems = validate_reports(data, repository, expected, report_paths)
    if problems:
        print("\n".join(problems))
        return 1
    print("清扫报告检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
