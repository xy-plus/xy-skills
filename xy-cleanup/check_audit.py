"""Validate cleanup reports against the repository map and file ownership."""
import argparse
import json
from functools import lru_cache
from pathlib import Path
import re
import sys

REPO_MAP_PATH = Path(__file__).resolve().parents[1] / "xy-repo-map"
HANDOFF_PATH = Path(__file__).resolve().parents[1] / "xy-handoff"
sys.path.insert(0, str(REPO_MAP_PATH))
sys.path.insert(0, str(HANDOFF_PATH))

from repo_map_lib.checker import check_repository
from repo_map_lib.git_repository import (ArgumentError, require_repository_root,
                                         tracked_paths)
from check_handoff import object_without_duplicate_keys
from audit_entities import collect_entities


ROLES = {"主审", "剃刀", "全图"}
KINDS = {"删除", "合并", "改名", "挪位置", "拆分", "重构", "修根因", "加约束", "改图", "其他"}
DECISIONS = {"直接做", "问你"}
ROOT_FIELDS = {"role", "repo", "nodes"}
FINDING_FIELDS = {
    "kind", "target", "problem", "proposal", "changes_behavior", "decision",
    "basis", "evidence", "benefit", "cost",
}
TEXT_FIELDS = {"target", "problem", "proposal", "decision", "evidence", "benefit", "cost"}
ENTITY_FIELDS = {"entity", "verdict", "reason"}
ENTITY_VERDICTS = {"无问题", "见发现"}


@lru_cache(maxsize=None)
def _numbered_rules(skill_path):
    """Read and index all numbered rules in one local skill document once."""
    try:
        lines = skill_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ()
    rules = []
    index = 0
    while index < len(lines):
        match = re.match(r"^\s*([1-9][0-9]*)\.\s", lines[index])
        if match is None:
            index += 1
            continue
        end = index + 1
        while end < len(lines) and re.match(r"^\s*[1-9][0-9]*\.\s", lines[end]) is None:
            end += 1
        rules.append((int(match.group(1)), "\n".join(lines[index:end])))
        index = end
    return tuple(rules)


def numbered_text(skill_path, number):
    """Return one numbered rule from a local skill document."""
    return dict(_numbered_rules(Path(skill_path))).get(number, "")


def basis_rule(basis):
    """Return the complete local rule named by a report's basis source."""
    if not isinstance(basis, dict):
        return ""
    source = basis.get("source")
    if not isinstance(source, str):
        return ""
    match = re.fullmatch(r"(xy-iron-law|xy-repo-rules) 第 ([1-9][0-9]*) 条", source)
    if match is None:
        return ""
    name = match.group(1)
    number = int(match.group(2))
    maximum = 3 if name == "xy-iron-law" else 7
    if number > maximum:
        return ""
    skill_path = Path(__file__).resolve().parents[1] / name / "SKILL.md"
    return numbered_text(skill_path, number)


def basis_is_supported(basis, rule):
    """Check that the quoted text occurs in the numbered local rule it names."""
    if not isinstance(basis, dict) or set(basis) != {"source", "text"}:
        return False
    text = basis.get("text")
    if not isinstance(text, str) or not text.strip():
        return False
    return bool(rule) and text in rule


def finding_is_valid(finding, label, problems):
    """Validate one proposed change and return whether it can affect coverage."""
    valid = True
    if not isinstance(finding, dict):
        problems.append(f"{label} 必须是对象")
        return False
    missing = FINDING_FIELDS - set(finding)
    extra = set(finding) - FINDING_FIELDS
    for field in sorted(missing):
        problems.append(f"{label} 缺少字段：{field}")
    for field in sorted(extra):
        problems.append(f"{label} 多余字段：{field}")
    if missing or extra:
        valid = False

    for field in sorted(TEXT_FIELDS):
        value = finding.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{label}.{field} 必须是非空字符串")
            valid = False
    kind = finding.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        problems.append(f"{label}.kind 不在允许值中")
        valid = False
    behavior = finding.get("changes_behavior")
    if not isinstance(behavior, bool):
        problems.append(f"{label}.changes_behavior 必须是布尔值")
        valid = False
    decision = finding.get("decision")
    if not isinstance(decision, str) or decision not in DECISIONS:
        problems.append(f"{label}.decision 不在允许值中")
        valid = False
    if isinstance(kind, str) and kind in KINDS and isinstance(behavior, bool):
        must_be_direct = kind in {"删除", "合并"} and behavior is False
        if isinstance(decision, str) and (decision == "直接做") != must_be_direct:
            problems.append(f"{label}.decision 与 kind、changes_behavior 不匹配")
            valid = False
        if kind == "改图" and decision != "问你":
            problems.append(f"{label}.kind 为改图时 decision 必须是问你")
            valid = False
    basis = finding.get("basis")
    rule = basis_rule(basis)
    if not basis_is_supported(basis, rule):
        message = f"{label}.basis.text 必须逐字来自 basis.source 指定的规范条款"
        if rule:
            message += f"；所标条款原文：\n{rule}"
        problems.append(message)
        valid = False
    return valid


def expand_scope(scope_values, model):
    """Resolve an explicit FQN list or the all-elements marker."""
    elements = model.get("elements", {}) if isinstance(model, dict) else {}
    if not isinstance(scope_values, list) or not scope_values:
        return [], ["--scope 必须显式给出节点 FQN 或「全部」"]
    if scope_values == ["全部"]:
        return sorted(elements), []
    if "全部" in scope_values:
        return [], ["「全部」不能和节点 FQN 同时使用"]
    problems = []
    selected = []
    for node in scope_values:
        if not isinstance(node, str) or not node.strip():
            problems.append("范围中的节点 FQN 必须是非空字符串")
        elif node in selected:
            problems.append(f"范围重复包含节点：{node}")
        elif node not in elements:
            problems.append(f"范围中的节点不存在：{node}")
        else:
            selected.append(node)
    return sorted(selected), problems


def expected_role_nodes(scope, role, direct_deletions):
    """Omit descendants covered by a direct whole-node deletion in this role."""
    omitted = set()
    deletions = direct_deletions.get(role, set())
    for node in scope:
        parts = node.split(".")
        ancestors = [".".join(parts[:index]) for index in range(1, len(parts))]
        if any(ancestor in deletions for ancestor in ancestors):
            omitted.add(node)
    return set(scope) - omitted


def validate_entity_list(value, label, expected, findings, problems):
    """Validate one report node's complete entity verdict list."""
    if not isinstance(value, list):
        problems.append(f"{label} 必须是数组")
        for entity_id in sorted(set(expected)):
            problems.append(f"{label} 缺少实体 {entity_id}")
        return
    actual = []
    seen = set()
    has_finding_verdict = False
    finding_targets = {
        finding.get("target") for finding in findings
        if isinstance(finding, dict) and isinstance(finding.get("target"), str)
    }
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{item_label} 必须是对象")
            continue
        missing = ENTITY_FIELDS - set(item)
        extra = set(item) - ENTITY_FIELDS
        for field in sorted(missing):
            problems.append(f"{item_label} 缺少字段：{field}")
        for field in sorted(extra):
            problems.append(f"{item_label} 多余字段：{field}")
        entity_id = item.get("entity")
        if not isinstance(entity_id, str) or not entity_id.strip():
            problems.append(f"{item_label}.entity 必须是非空字符串")
        else:
            actual.append(entity_id)
            if entity_id in seen:
                problems.append(f"{label} 实体 {entity_id} 重复")
            seen.add(entity_id)
        verdict = item.get("verdict")
        if not isinstance(verdict, str) or verdict not in ENTITY_VERDICTS:
            problems.append(f"{item_label}.verdict 不在允许值中")
        elif verdict == "见发现":
            has_finding_verdict = True
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"{item_label}.reason 必须是非空字符串")
        elif verdict == "见发现" and reason not in finding_targets:
            problems.append(f"{item_label}.reason 必须等于本节点某条 finding 的 target")

    for entity_id in sorted(set(expected) - set(actual)):
        problems.append(f"{label} 缺少实体 {entity_id}")
    for entity_id in sorted(set(actual) - set(expected)):
        problems.append(f"{label} 多余实体 {entity_id}")
    if has_finding_verdict and not findings:
        problems.append(f"{label} 标为见发现时节点 findings 不能为空")


def validate_report_node(entry, role, report_name, scope, owners, expected_entities,
                         direct_deletion_targets, problems):
    """Validate a node entry and its exact file list for a report role."""
    if not isinstance(entry, dict):
        problems.append(f"{report_name} 的 nodes 项必须是对象")
        return None
    required = {"node", "findings", "checked"}
    if role in {"主审", "剃刀"}:
        required.update({"files", "entities"})
    missing = required - set(entry)
    extra = set(entry) - required
    for field in sorted(missing):
        problems.append(f"{report_name} 的节点缺少字段：{field}")
    for field in sorted(extra):
        if role == "全图" and field in {"files", "entities"}:
            problems.append(f"{report_name} 的全图节点不能列 {field}")
        else:
            problems.append(f"{report_name} 的节点多余字段：{field}")
    node = entry.get("node")
    if not isinstance(node, str) or not node.strip():
        problems.append(f"{report_name} 的节点 FQN 必须是非空字符串")
        return None
    if node not in scope:
        problems.append(f"{report_name} 报告了范围外或不存在的节点：{node}")

    checked = entry.get("checked")
    findings = entry.get("findings")
    if not isinstance(checked, str):
        problems.append(f"{report_name} 的 {node}.checked 必须是字符串")
    if not isinstance(findings, list):
        problems.append(f"{report_name} 的 {node}.findings 必须是数组")
        findings = []
    if not findings and isinstance(checked, str) and not checked.strip():
        problems.append(f"{report_name} 没有发现的节点必须写明 checked")
    if role in {"主审", "剃刀"}:
        entity_label = f"{report_name} 的 {node}.entities"
        if "entities" in entry:
            validate_entity_list(
                entry["entities"], entity_label,
                expected_entities.get(node, []), findings, problems
            )
        else:
            for entity_id in sorted(set(expected_entities.get(node, []))):
                problems.append(f"{entity_label} 缺少实体 {entity_id}")
    for index, finding in enumerate(findings):
        valid = finding_is_valid(finding, f"{report_name} 的 {node}.findings[{index}]", problems)
        if not valid or not isinstance(finding, dict):
            continue
        target = finding.get("target")
        if (finding.get("kind") == "删除" and finding.get("decision") == "直接做"
                and finding.get("changes_behavior") is False and target == node):
            direct_deletion_targets.add(target)

    if role in {"主审", "剃刀"}:
        files = entry.get("files")
        if not isinstance(files, list) or any(not isinstance(path, str) for path in files):
            problems.append(f"{report_name} 的 {node}.files 必须是字符串数组")
        elif len(files) != len(set(files)):
            problems.append(f"{report_name} 的 {node}.files 不能重复")
        else:
            expected = sorted(path for path, owner in owners.items()
                              if owner.element == node)
            if sorted(files) != expected:
                problems.append(f"{report_name} 的 {node}.files 与文件归属不一致")
    return node


def validate_reports(repository, scope, reports, model, owners, expected_entities):
    """Return all schema, coverage, evidence, and file-ownership problems."""
    problems = []
    expected_repository = str(repository)
    elements = model.get("elements", {}) if isinstance(model, dict) else {}
    role_counts = {role: {} for role in ROLES}
    direct_deletions = {role: set() for role in ROLES}
    whole_report_count = 0

    if not isinstance(reports, list):
        return ["报告输入必须是列表"]
    for index, report_input in enumerate(reports):
        if not isinstance(report_input, dict):
            problems.append(f"报告 {index + 1} 输入必须包含 path 和 data")
            continue
        label = report_input.get("path", f"报告 {index + 1}")
        document = report_input.get("data")
        if not isinstance(document, dict):
            problems.append(f"{label} 必须是 JSON 对象")
            continue
        missing = ROOT_FIELDS - set(document)
        extra = set(document) - ROOT_FIELDS
        for field in sorted(missing):
            problems.append(f"{label} 缺少字段：{field}")
        for field in sorted(extra):
            problems.append(f"{label} 多余字段：{field}")
        role = document.get("role")
        if not isinstance(role, str) or role not in ROLES:
            problems.append(f"{label}.role 必须是主审、剃刀或全图")
            continue
        if role == "全图":
            whole_report_count += 1
        report_repository = document.get("repo")
        if report_repository != expected_repository:
            problems.append(f"{label}.repo 必须等于本次清扫的仓路径：{expected_repository}")
        entries = document.get("nodes")
        if not isinstance(entries, list):
            problems.append(f"{label}.nodes 必须是数组")
            continue
        seen_here = set()
        for entry in entries:
            node = validate_report_node(
                entry, role, label, scope, owners, expected_entities,
                direct_deletions[role], problems
            )
            if node is None:
                continue
            if node in seen_here:
                problems.append(f"{label} 重复列出节点：{node}")
            seen_here.add(node)
            role_counts[role][node] = role_counts[role].get(node, 0) + 1
    for role in ROLES:
        direct_deletions[role].intersection_update(elements)

    if whole_report_count != 1:
        problems.append(f"全图报告必须恰好一份，实际 {whole_report_count} 份")
    for role in sorted(ROLES):
        expected = expected_role_nodes(scope, role, direct_deletions)
        for node in sorted(expected):
            count = role_counts[role].get(node, 0)
            if count != 1:
                problems.append(f"{role}报告中的节点 {node} 必须恰好出现一次，实际 {count} 次")
        for node in sorted(set(role_counts[role]) - expected):
            if node in scope:
                problems.append(f"{role}报告中的节点 {node} 被祖先删除项豁免后不应再出现")
    return problems


def load_reports(paths):
    """Read each report file as one JSON object and preserve its path for errors."""
    reports = []
    problems = []
    for path in paths:
        try:
            document = json.loads(
                Path(path).read_text(encoding="utf-8"),
                object_pairs_hook=object_without_duplicate_keys,
            )
        except (OSError, UnicodeError, ValueError) as error:
            problems.append(f"无法读取报告 {path}：{error}")
            continue
        reports.append({"path": str(path), "data": document})
    return reports, problems


def parser():
    """Build explicit entity-list and report-check subcommands."""
    command = argparse.ArgumentParser(description="生成或核对清扫审查实体清单")
    actions = command.add_subparsers(dest="action", required=True)
    entities = actions.add_parser("entities", help="打印范围内各节点的实体 ID")
    check = actions.add_parser("check", help="核对审查报告")
    for action in (entities, check):
        action.add_argument("--repo", required=True, help="仓根绝对路径")
        action.add_argument(
            "--scope", required=True, nargs="+", help="节点 FQN 列表或「全部」"
        )
    check.add_argument("--reports", required=True, nargs="+", help="JSON 报告路径列表")
    return command


def repository_context(repository):
    """Require a valid graph without requiring its approval record."""
    root = require_repository_root(repository)
    problems, model, owners = check_repository(root, check_approval=False)
    if problems:
        raise ValueError("\n".join(problems))
    tracked = tracked_paths(root)
    return root, model, tracked, owners


def main(argv):
    """Print expected entities or validate reports and return a command status."""
    try:
        arguments = parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    try:
        root, model, _, owners = repository_context(arguments.repo)
    except ArgumentError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    scope, scope_problems = expand_scope(arguments.scope, model)
    if scope_problems:
        print("\n".join(scope_problems))
        return 2
    try:
        expected_entities = collect_entities(root, owners, scope)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    if arguments.action == "entities":
        print(json.dumps({"repo": str(root), "nodes": expected_entities},
                         ensure_ascii=False, indent=2))
        return 0
    reports, read_problems = load_reports(arguments.reports)
    if read_problems:
        print("\n".join(read_problems))
        return 1
    problems = validate_reports(
        root, scope, reports, model, owners, expected_entities
    )
    if problems:
        print("\n".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
