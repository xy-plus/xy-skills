"""Validate cleanup reports against the repository map and file ownership."""
import argparse
import json
from pathlib import Path
import re
import sys

REPO_MAP_PATH = Path(__file__).resolve().parents[1] / "xy-repo-map"
sys.path.insert(0, str(REPO_MAP_PATH))

from repo_map_lib.checker import check_repository
from repo_map_lib.git_repository import (ArgumentError, discover_subrepositories,
                                         gitlink_paths, require_repository_root,
                                         tracked_paths)
from repo_map_lib.ownership import check_ownership


ROLES = {"主审", "剃刀", "全图"}
KINDS = {"删除", "合并", "改名", "挪位置", "拆分", "重构", "修根因", "加约束", "改图", "其他"}
DECISIONS = {"直接做", "问你"}
ROOT_FIELDS = {"role", "repo", "nodes"}
FINDING_FIELDS = {
    "kind", "target", "problem", "proposal", "changes_behavior", "decision",
    "basis", "evidence", "benefit", "cost",
}
TEXT_FIELDS = {"target", "problem", "proposal", "decision", "evidence", "benefit", "cost"}


def numbered_text(skill_path, number):
    """Return one numbered rule from a local skill document."""
    try:
        lines = skill_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ""
    start = None
    for index, line in enumerate(lines):
        if re.match(rf"^\s*{number}\.\s", line):
            start = index
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"^\s*[1-9][0-9]*\.\s", lines[index]):
            end = index
            break
    return "\n".join(lines[start:end])


def basis_is_supported(basis):
    """Check that the quoted text occurs in the numbered local rule it names."""
    if not isinstance(basis, dict) or set(basis) != {"source", "text"}:
        return False
    source = basis.get("source")
    text = basis.get("text")
    if not isinstance(source, str) or not isinstance(text, str) or not text.strip():
        return False
    match = re.fullmatch(r"(xy-iron-law|xy-repo-rules) 第 ([1-9][0-9]*) 条", source)
    if match is None:
        return False
    name = match.group(1)
    number = int(match.group(2))
    maximum = 3 if name == "xy-iron-law" else 7
    if number > maximum:
        return False
    skill_path = Path(__file__).resolve().parents[1] / name / "SKILL.md"
    rule = numbered_text(skill_path, number)
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
    if not basis_is_supported(finding.get("basis")):
        problems.append(f"{label}.basis.text 必须逐字来自 basis.source 指定的规范条款")
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


def validate_report_node(entry, role, report_name, scope, owners, direct_deletion_targets, problems):
    """Validate a node entry and its exact file list for a report role."""
    if not isinstance(entry, dict):
        problems.append(f"{report_name} 的 nodes 项必须是对象")
        return None
    required = {"node", "findings", "checked"}
    if role in {"主审", "剃刀"}:
        required.add("files")
    missing = required - set(entry)
    extra = set(entry) - required
    for field in sorted(missing):
        problems.append(f"{report_name} 的节点缺少字段：{field}")
    for field in sorted(extra):
        if role == "全图" and field == "files":
            problems.append(f"{report_name} 的全图节点不能列 files")
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
            expected = sorted(path for path, owner in owners.items() if owner == node)
            if sorted(files) != expected:
                problems.append(f"{report_name} 的 {node}.files 与文件归属不一致")
    return node


def validate_reports(repository, scope_values, reports, model, owners):
    """Return all schema, coverage, evidence, and file-ownership problems."""
    problems = []
    scope, scope_problems = expand_scope(scope_values, model)
    problems.extend(scope_problems)
    expected_repository = str(Path(repository).resolve())
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
                entry, role, label, scope, owners, direct_deletions[role], problems
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
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            problems.append(f"无法读取报告 {path}：{error}")
            continue
        reports.append({"path": str(path), "data": document})
    return reports, problems


def parser():
    """Build the checker CLI; every input is required explicitly."""
    command = argparse.ArgumentParser(description="核对清扫报告与仓图文件归属")
    command.add_argument("--repo", required=True, help="仓根绝对路径")
    command.add_argument("--scope", required=True, nargs="+", help="节点 FQN 列表或「全部」")
    command.add_argument("--reports", required=True, nargs="+", help="JSON 报告路径列表")
    return command


def repository_context(repository):
    """Require a valid graph without requiring its approval record."""
    root = require_repository_root(repository)
    problems, model = check_repository(root, check_approval=False)
    if problems:
        raise ValueError("\n".join(problems))
    tracked = tracked_paths(root)
    ownership_problems, owners = check_ownership(
        root, model, tracked, gitlink_paths(root), discover_subrepositories(root)
    )
    if ownership_problems:
        raise ValueError("\n".join(ownership_problems))
    return root, model, owners


def main(argv):
    """Run the report checker and return the documented command status."""
    try:
        arguments = parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    if not Path(arguments.repo).is_absolute():
        print("--repo 必须是绝对路径", file=sys.stderr)
        return 2
    try:
        root, model, owners = repository_context(arguments.repo)
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
    reports, read_problems = load_reports(arguments.reports)
    if read_problems:
        print("\n".join(read_problems))
        return 1
    problems = validate_reports(root, scope, reports, model, owners)
    if problems:
        print("\n".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
