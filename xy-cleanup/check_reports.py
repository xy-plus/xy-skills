import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "xy-repo-map"))
sys.path.insert(0, str(REPOSITORY / "xy-review"))
from repo_map_lib.git_repository import ArgumentError  # noqa: E402
from repo_map_lib.git_repository import tracked_paths  # noqa: E402
from repo_map_lib.map_data import build_map, file_category  # noqa: E402
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
    "主审": {
        "feature", "what_true", "why_holds", "structure", "todo_items", "findings",
    },
    "剃刀": {"feature", "necessity", "smallest", "keep", "todo_items", "findings"},
    "剃刀复审": {"feature", "verdicts", "findings"},
}
FINDING_KEYS = {
    "kind", "target", "problem", "proposal", "changes_behavior", "decision",
    "basis", "evidence", "benefit", "cost",
}
BASIS_KEYS = {"source", "text"}
TODO_ITEM_KEYS = {"lines", "needed", "smaller", "why_holds"}
KEEP_ITEM_KEYS = {"lines", "requirement", "achieved", "cost"}
RECHECK_VERDICT_KEYS = {"lines", "attempt", "evidence", "verdict"}
ROLES = {"主审", "剃刀"}
REPORT_ROLES = ROLES | {"剃刀复审"}
KINDS = {"删除", "合并", "改名", "挪位置", "拆分", "重构", "修根因", "加约束", "改清单", "其他"}
DECISIONS = {"直接做", "问你"}
TODO_FILENAME = "TODO.md"  # 用户各仓的 TODO 都叫 TODO.md。
CODE_DEFINITION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
MARKDOWN_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")
SETEXT_UNDERLINE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")


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


SMALLEST_LINE_KEYS = {"code", "docs", "comments", "tests"}


def _validate_smallest(review, label, feature_id, feature_data, findings):
    problems = _field_errors(
        review.get("smallest"), {"design", "lines"}, f"{label}.smallest",
    )
    smallest = review.get("smallest")
    if not isinstance(smallest, dict):
        return problems
    if not _nonempty(smallest.get("design")):
        problems.append(f"{label}.smallest.design 必须是非空字符串")
    lines = smallest.get("lines")
    problems.extend(_field_errors(lines, SMALLEST_LINE_KEYS, f"{label}.smallest.lines"))
    if not isinstance(lines, dict):
        return problems
    for key in sorted(SMALLEST_LINE_KEYS & lines.keys()):
        value = lines[key]
        if type(value) is not int or value < 0:
            problems.append(
                f"{label}.smallest.lines.{key} 必须是非负整数"
            )
    if not SMALLEST_LINE_KEYS <= lines.keys() or any(
        type(lines[key]) is not int or lines[key] < 0
        for key in SMALLEST_LINE_KEYS & lines.keys()
    ):
        return problems

    current = feature_data["lines"]["own"]
    estimate_total = sum(lines[key] for key in SMALLEST_LINE_KEYS)
    current_total = sum(current[key] for key in SMALLEST_LINE_KEYS)
    has_feature_reduction = any(
        isinstance(finding, dict)
        and finding.get("target") == feature_id
        and finding.get("kind") in {"删除", "合并", "重构"}
        for finding in findings
    )
    if estimate_total < current_total and not has_feature_reduction:
        line_keys = ("code", "docs", "comments", "tests")
        current_lines = ", ".join(f"{key}={current[key]}" for key in line_keys)
        estimated_lines = ", ".join(f"{key}={lines[key]}" for key in line_keys)
        problems.append(
            f"{label}.smallest 估算少于当前行数，却没有目标为本功能 id、kind 为删除、合并或重构的发现；"
            f"当前 {current_lines}，估计 {estimated_lines}"
        )
    return problems


def _parse_line_range(value, allow_single_line=False):
    if not isinstance(value, str):
        return None
    path, separator, numbers = value.rpartition(":")
    if not separator or not path:
        return None
    parts = numbers.split("-")
    if allow_single_line and len(parts) == 1:
        parts *= 2
    if (len(parts) != 2
            or any(not part.isascii() or not part.isdecimal() for part in parts)):
        return None
    try:
        start_line, end_line = (int(part) for part in parts)
    except ValueError:
        return None
    return path, start_line, end_line


def _checked_line_range(value, label, field, repository, allowed_files,
                        lines_by_file, allow_single_line=False):
    parsed = _parse_line_range(value, allow_single_line=allow_single_line)
    format_text = "文件:行号或 文件:起始行-结束行" if allow_single_line else "路径:起始行-结束行"
    if parsed is None:
        return None, [f"{label}.{field} 必须写成 {format_text}：{value}"]
    path, start_line, end_line = parsed
    display_range = f"{path}:{start_line}-{end_line}"
    if path not in allowed_files:
        return None, [f"{label}.{field} 文件不在允许范围内：{display_range}"]
    if path not in lines_by_file:
        lines_by_file[path] = _read_file_lines(Path(repository) / path)
    file_lines = lines_by_file[path]
    if file_lines is None:
        return None, [f"{label}.{field} 无法读取文件：{display_range}"]
    if start_line < 1 or start_line > end_line or end_line > len(file_lines):
        return None, [
            f"{label}.{field} 行范围无效：{display_range}（文件共 {len(file_lines)} 行）"
        ]
    return parsed, []


def _record_line_range(ranges_by_file, line_range, label, field):
    path, start_line, end_line = line_range
    previous_ranges = ranges_by_file.setdefault(path, [])
    overlapping = next(
        ((previous_start, previous_end)
         for previous_start, previous_end in previous_ranges
         if start_line <= previous_end and previous_start <= end_line),
        None,
    )
    previous_ranges.append((start_line, end_line))
    if overlapping is not None:
        return f"{label}.{field} 与同一文件的行范围重叠：{path}:{start_line}-{end_line}"
    return None


def _uncovered_nonempty_lines(paths, ranges_by_file, label, description,
                              repository, lines_by_file):
    problems = []
    for path in sorted(paths):
        if path not in lines_by_file:
            lines_by_file[path] = _read_file_lines(Path(repository) / path)
        file_lines = lines_by_file[path]
        if file_lines is None:
            problems.append(f"{label} 无法读取{description}文件：{path}")
            continue
        covered_lines = {
            line_number
            for start_line, end_line in ranges_by_file.get(path, [])
            for line_number in range(start_line, end_line + 1)
        }
        for line_number, line in enumerate(file_lines, start=1):
            if _line_is_nonempty(line) and line_number not in covered_lines:
                problems.append(
                    f"{label} 未覆盖{description}的非空行：{path}:{line_number}"
                )
    return problems


def _validate_finding(finding, label, tracked_todo_files, force_ask_user):
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

    if force_ask_user:
        # A recheck challenges the first Razor's keep decision, so both conclusions go to the user.
        if decision != "问你":
            problems.append(f"{label}.decision 剃刀复审的决定必须是 问你")
    elif (isinstance(changes_behavior, bool) and isinstance(decision, str)
            and decision in DECISIONS and isinstance(kind, str) and kind in KINDS):
        target_range = _parse_line_range(finding["target"], allow_single_line=True)
        target_is_todo = target_range is not None and target_range[0] in tracked_todo_files
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


def _tracked_location_patterns(tracked_files):
    return [
        (
            path,
            re.compile(
                rf"(?<![A-Za-z0-9_./-]){re.escape(path)}:"
                r"(?P<start>[0-9]+)(?:-(?P<end>[0-9]+))?"
                r"(?![A-Za-z0-9_/-])(?!\.[0-9])"
            ),
        )
        for path in sorted(tracked_files, key=lambda value: (-len(value), value))
    ]


def _has_external_evidence_location(evidence, verdict_range, label, repository,
                                    tracked_files, lines_by_file, location_patterns):
    own_path, own_start, own_end = verdict_range
    for _path, pattern in location_patterns:
        for match in pattern.finditer(evidence):
            location, errors = _checked_line_range(
                match.group(0), label, "evidence", repository, tracked_files,
                lines_by_file, allow_single_line=True,
            )
            if errors or location is None:
                continue
            path, start_line, end_line = location
            overlaps_own_lines = (
                path == own_path
                and start_line <= own_end
                and own_start <= end_line
            )
            if not overlaps_own_lines:
                return True
    return False


def _validate_recheck_verdicts(verdicts, label, keep_lines, finding_ranges,
                               feature_targets, repository, tracked_files,
                               lines_by_file, location_patterns):
    if not isinstance(verdicts, list):
        return [f"{label}.verdicts 必须是数组"]

    problems = []
    verdict_counts = {}
    nonstanding = []
    for index, verdict in enumerate(verdicts):
        verdict_label = f"{label}.verdicts[{index}]"
        item_problems = _field_errors(verdict, RECHECK_VERDICT_KEYS, verdict_label)
        problems.extend(item_problems)
        if item_problems or not isinstance(verdict, dict):
            continue

        lines = verdict["lines"]
        if not isinstance(lines, str):
            problems.append(f"{verdict_label}.lines 必须与首轮 keep 的 lines 完全相同的字符串")
        else:
            verdict_counts[lines] = verdict_counts.get(lines, 0) + 1
        if not _nonempty(verdict["attempt"]):
            problems.append(f"{verdict_label}.attempt 必须是非空字符串")
        evidence = verdict["evidence"]
        if not _nonempty(evidence):
            problems.append(f"{verdict_label}.evidence 必须是非空字符串")
        elif isinstance(lines, str):
            line_range = _parse_line_range(lines)
            if line_range is not None and not _has_external_evidence_location(
                evidence,
                line_range,
                verdict_label,
                repository,
                tracked_files,
                lines_by_file,
                location_patterns,
            ):
                problems.append(
                    f"{verdict_label}.evidence 必须引至少一处有效位置（本实体行范围以外）：{lines}"
                )
        if (not isinstance(verdict["verdict"], str)
                or verdict["verdict"] not in {"站得住", "站不住"}):
            problems.append(
                f"{verdict_label}.verdict 必须是 站得住 或 站不住：{verdict['verdict']!r}"
            )
        elif verdict["verdict"] == "站不住" and isinstance(lines, str):
            nonstanding.append((lines, verdict_label))

    keep_counts = {}
    for lines in keep_lines:
        keep_counts[lines] = keep_counts.get(lines, 0) + 1
    for lines, expected_count in keep_counts.items():
        actual_count = verdict_counts.get(lines, 0)
        if actual_count < expected_count:
            problems.append(
                f"{label}.verdicts 缺少 keep 的判定：{lines}（应有 {expected_count} 条，实有 {actual_count} 条）"
            )
        elif actual_count > expected_count:
            problems.append(f"{label}.verdicts 重复判定 keep：{lines}")
    for lines in verdict_counts:
        if lines not in keep_counts:
            problems.append(f"{label}.verdicts 多出不在 keep 里的判定：{lines}")

    for lines, verdict_label in nonstanding:
        verdict_range = _parse_line_range(lines)
        overlaps_finding = False
        feature_reduction = any(
            finding.get("kind") in {"删除", "合并"}
            for finding in feature_targets
        )
        if verdict_range is not None:
            path, start_line, end_line = verdict_range
            overlaps_finding = any(
                path == finding_range[0]
                and start_line <= finding_range[2]
                and finding_range[1] <= end_line
                for _finding, finding_range in finding_ranges
            )
        if not overlaps_finding and not feature_reduction:
            problems.append(
                f"{verdict_label} 判站不住时必须有重叠的发现目标：{lines}"
            )
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
    checked, _ = _checked_line_range(
        target, "target", "target", repository, tracked_files, {},
        allow_single_line=True,
    )
    return checked is not None


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
        for key in ("lines", "needed", "smaller", "why_holds"):
            if not _nonempty(item.get(key)):
                problems.append(f"{item_label}.{key} 必须是非空字符串")
        line_spec = item["lines"]
        if not _nonempty(line_spec):
            continue
        line_range, range_problems = _checked_line_range(
            line_spec, item_label, "lines", repository, direct_todo_files,
            lines_by_file,
        )
        problems.extend(range_problems)
        if line_range is None:
            continue
        overlapping = _record_line_range(
            occupied_by_file, line_range, item_label, "lines",
        )
        if overlapping is not None:
            problems.append(overlapping)

    problems.extend(_uncovered_nonempty_lines(
        direct_todo_files, occupied_by_file, label, "直属 TODO ",
        repository, lines_by_file,
    ))
    return problems


def _validate_requirement(requirement, label, feature):
    problems = _field_errors(requirement, BASIS_KEYS, label)
    if not isinstance(requirement, dict):
        return problems
    for key in BASIS_KEYS:
        if key in requirement and not _nonempty(requirement[key]):
            problems.append(f"{label}.{key} 必须是非空字符串")
    if set(requirement) != BASIS_KEYS or not all(
        _nonempty(requirement.get(key)) for key in BASIS_KEYS
    ):
        return problems

    source = requirement["source"]
    text = requirement["text"]
    if source == "要什么":
        expected_text = feature["what"]
    elif source == "为什么":
        expected_text = feature["why"]["text"]
    elif basis_is_supported(requirement):
        return problems
    else:
        problems.append(
            f"{label}.source 必须是 要什么、为什么，或逐字引用有效的用户规范条款"
        )
        return problems
    if text not in expected_text:
        problems.append(f"{label}.text 必须逐字引用来源中的原文：{source}")
    return problems


def _python_definition_lines(path, file_lines):
    source = b"\n".join(
        line if isinstance(line, bytes) else str(line).encode("utf-8")
        for line in file_lines
    )
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, UnicodeDecodeError):
        return None

    definition_lines = set()

    def inspect_class_body(nodes):
        for node in nodes:
            if isinstance(node, CODE_DEFINITION_TYPES):
                definition_lines.add(node.lineno)
                if isinstance(node, ast.ClassDef):
                    inspect_class_body(node.body)

    inspect_class_body(tree.body)
    return definition_lines


def _strip_markdown_container_prefixes(text):
    remaining = text
    while True:
        quote = re.match(r"^ {0,3}>[ \t]?", remaining)
        if quote is not None:
            remaining = remaining[quote.end():]
            continue
        list_item = re.match(
            r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)", remaining,
        )
        if list_item is not None:
            remaining = remaining[list_item.end():]
            continue
        return remaining


def _markdown_heading_lines(file_lines):
    headings = set()
    texts = [
        line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
        for line in file_lines
    ]
    hidden_lines = set()
    yaml_header_end = None
    if texts and texts[0].strip() == "---":
        yaml_header_end = next(
            (index for index in range(1, len(texts)) if texts[index].strip() == "---"),
            None,
        )
    fence_character = None
    fence_length = 0
    for index, text in enumerate(texts):
        if yaml_header_end is not None and index <= yaml_header_end:
            hidden_lines.add(index)
            continue
        container_text = _strip_markdown_container_prefixes(text)
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", container_text)
        if fence_character is not None:
            hidden_lines.add(index)
            closing = re.match(
                r"^ {0,3}(`{3,}|~{3,})[ \t]*$", container_text,
            )
            if (closing is not None and closing.group(1)[0] == fence_character
                    and len(closing.group(1)) >= fence_length):
                fence_character = None
                fence_length = 0
            continue
        if fence is not None:
            hidden_lines.add(index)
            fence_character = fence.group(1)[0]
            fence_length = len(fence.group(1))
            continue
        if MARKDOWN_HEADING.match(container_text):
            headings.add(index + 1)

    for index, text in enumerate(texts):
        if index in hidden_lines or not SETEXT_UNDERLINE.match(text):
            continue
        if re.match(r"^ {0,3}-(?:[ \t]+|$)", text):
            continue
        if index == 0 or index - 1 in hidden_lines:
            continue
        paragraph = texts[index - 1]
        if not paragraph.strip() or MARKDOWN_HEADING.match(paragraph):
            continue
        # List items, table rows, and indented code are block content, not Setext text.
        if (paragraph.startswith(("    ", "\t"))
                or re.match(r"^ {0,3}(?:[-+*]|\d+[.)])(?:[ \t]+|$)", paragraph)
                or re.match(r"^ {0,3}>", paragraph)
                or SETEXT_UNDERLINE.match(paragraph)):
            continue
        headings.add(index)
    return headings


def _entity_range_problem(path, start_line, end_line, file_lines, label):
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        entity_lines = _python_definition_lines(path, file_lines)
    elif suffix == ".md":
        entity_lines = _markdown_heading_lines(file_lines)
    else:
        return None
    if entity_lines is None:
        return f"{label}.lines 无法检查一条 keep 是否只覆盖一个实体：{path}"
    if sum(
        start_line <= line_number <= end_line for line_number in entity_lines
    ) > 1:
        return f"{label}.lines 一条 keep 只能覆盖一个实体：{path}:{start_line}-{end_line}"
    return None


def _validate_keep_items(items, label, repository, feature, keep_files,
                         finding_ranges, feature_targets):
    problems = []
    if not isinstance(items, list):
        return [f"{label} 必须是数组"]

    lines_by_file = {}
    keep_ranges_by_file = {}
    for item_index, item in enumerate(items):
        item_label = f"{label}[{item_index}]"
        item_problems = _field_errors(item, KEEP_ITEM_KEYS, item_label)
        problems.extend(item_problems)
        if item_problems or not isinstance(item, dict):
            continue
        for key in ("lines", "achieved", "cost"):
            if not _nonempty(item.get(key)):
                problems.append(f"{item_label}.{key} 必须是非空字符串")
        problems.extend(_validate_requirement(
            item.get("requirement"), f"{item_label}.requirement", feature,
        ))
        line_spec = item.get("lines")
        if not _nonempty(line_spec):
            continue
        line_range, range_problems = _checked_line_range(
            line_spec, item_label, "lines", repository, keep_files,
            lines_by_file,
        )
        problems.extend(range_problems)
        if line_range is None:
            continue
        overlapping = _record_line_range(
            keep_ranges_by_file, line_range, item_label, "lines",
        )
        if overlapping is not None:
            problems.append(overlapping)
        path, start_line, end_line = line_range
        entity_problem = _entity_range_problem(
            path, start_line, end_line, lines_by_file[path], item_label,
        )
        if entity_problem is not None:
            problems.append(entity_problem)

    for finding in feature_targets:
        if finding.get("kind") == "删除" and keep_ranges_by_file:
            problems.append(
                f"{label} 的删除目标和 keep 行范围重叠：{finding['target']}"
            )

    # 留下依据只认功能清单的「要什么」「为什么」或已支持的规范；原话可能是问句或带条件的回答。
    # 起草的理由还未确认，不能给实体作为定了的要求。
    if feature["why"]["source"] == "起草":
        if items:
            problems.append(f"{label} 的功能 why.source 为 起草时 keep 必须是空数组")
        return problems

    for finding, target_range in finding_ranges:
        if finding.get("kind") != "删除":
            continue
        target_path, target_start, target_end = target_range
        for keep_start, keep_end in keep_ranges_by_file.get(target_path, []):
            if target_start <= keep_end and keep_start <= target_end:
                problems.append(
                    f"{label} 的删除目标和 keep 行范围重叠：{finding['target']}"
                )

    covered_ranges = {
        path: list(ranges) for path, ranges in keep_ranges_by_file.items()
    }
    feature_reduction_targets = [
        feature_finding for feature_finding in feature_targets
        if feature_finding.get("kind") in {"删除", "合并"}
    ]
    if feature_reduction_targets:
        # A finding on this feature id covers every direct file; read each file so unreadable files still fail.
        for path in keep_files:
            if path not in lines_by_file:
                lines_by_file[path] = _read_file_lines(Path(repository) / path)
            file_lines = lines_by_file[path]
            if file_lines is not None:
                covered_ranges[path] = [(1, len(file_lines))]
    for _finding, target_range in finding_ranges:
        path, start_line, end_line = target_range
        if path in keep_files:
            covered_ranges.setdefault(path, []).append((start_line, end_line))
    problems.extend(_uncovered_nonempty_lines(
        keep_files, covered_ranges, label, "功能文件 ", repository, lines_by_file,
    ))
    return problems


def validate_reports(data, repository, expected_features, report_paths, tracked_files):
    """Return all structural, coverage, decision, and rule-quote problems."""
    problems = []
    counts = {role: {} for role in ROLES}
    recheck_counts = {}
    required_rechecks = set()
    recheck_report_seen = False
    razor_keep_lines = {}
    pending_recheck_verdicts = []
    evidence_lines_by_file = {}
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
        if not isinstance(role, str) or role not in REPORT_ROLES:
            problems.append(f"报告 {label} 的 role 必须是 主审、剃刀 或 剃刀复审")
        elif role == "剃刀复审":
            recheck_report_seen = True
        expected_repository = str(Path(repository).resolve())
        if document.get("repo") != expected_repository:
            problems.append(f"报告 {label} 的 repo 必须等于 {expected_repository}")
        review_features = document.get("features")
        if not isinstance(review_features, list):
            problems.append(f"报告 {label} 的 features 必须是数组")
            continue
        if role == "剃刀复审" and len(review_features) != 1:
            # Large batches produced formulaic reviews, so split by feature and require checkable positions.
            problems.append(f"报告 {label} 的剃刀复审 features 必须恰有一个功能对象，实际 {len(review_features)} 个")
        for feature_index, review in enumerate(review_features):
            feature_label = f"报告 {label} 的 features[{feature_index}]"
            if not isinstance(review, dict):
                problems.append(f"{feature_label}必须是对象")
                continue
            role_keys = FEATURE_KEYS.get(role) if isinstance(role, str) else None
            if role_keys is not None:
                problems.extend(_field_errors(review, role_keys, feature_label))
            feature_id = review.get("feature")
            if not isinstance(feature_id, str) or feature_id not in data["features"]:
                problems.append(f"{feature_label}.feature 不在功能清单中：{feature_id!r}")
            elif isinstance(role, str) and role in ROLES and feature_id not in expected_features:
                problems.append(f"报告功能不在本次审查名单里：{feature_id}")
            elif isinstance(role, str) and role in ROLES:
                counts[role][feature_id] = counts[role].get(feature_id, 0) + 1
            elif role == "剃刀复审":
                recheck_counts[feature_id] = recheck_counts.get(feature_id, 0) + 1
            if role == "主审":
                for key in ("what_true", "why_holds", "structure"):
                    if not _nonempty(review.get(key)):
                        problems.append(f"{feature_label}.{key} 必须是非空字符串")
            elif role == "剃刀" and not _nonempty(review.get("necessity")):
                problems.append(f"{feature_label}.necessity 必须是非空字符串")

            feature_data = (
                data["features"].get(feature_id)
                if isinstance(feature_id, str) else None
            )
            if role == "剃刀" and isinstance(feature_id, str):
                source_keep = review.get("keep")
                if feature_id not in razor_keep_lines:
                    razor_keep_lines[feature_id] = [
                        item["lines"]
                        for item in source_keep
                        if isinstance(item, dict) and isinstance(item.get("lines"), str)
                    ] if isinstance(source_keep, list) else []
            if feature_data is not None:
                direct_files = {
                    path
                    for group_paths in feature_data["files"].values()
                    for path in group_paths
                }
                direct_todo_files = {
                    path for path in direct_files if Path(path).name == TODO_FILENAME
                }
                keep_files = (
                    {
                        path for path in direct_files
                        if Path(path).name != TODO_FILENAME
                        and file_category(repository, path) in {"code", "tests", "docs"}
                    }
                    if role in ("剃刀", "剃刀复审") else set()
                )
            else:
                direct_files = set()
                direct_todo_files = set()
                keep_files = set()
            if isinstance(role, str) and role in ROLES and "todo_items" in review:
                problems.extend(_validate_todo_items(
                    review["todo_items"],
                    f"报告 {label} 的功能 {feature_id}.todo_items",
                    repository,
                    direct_todo_files,
                ))
            findings = review.get("findings")
            if not isinstance(findings, list):
                problems.append(f"{feature_label}.findings 必须是数组")
                findings = []
            finding_ranges = []
            feature_targets = []
            for finding_index, finding in enumerate(findings):
                finding_label = f"{feature_label}.findings[{finding_index}]"
                problems.extend(_validate_finding(
                    finding, finding_label, tracked_todo_files,
                    force_ask_user=role == "剃刀复审",
                ))
                if not isinstance(finding, dict) or not _nonempty(finding.get("target")):
                    continue
                target = finding["target"]
                if not _valid_target(target, data, repository, tracked_files):
                    problems.append(
                        f"{finding_label}.target 必须是功能 id、文件:行号或 文件:起始行-结束行："
                        f"{target}"
                    )
                    continue
                target_range = _parse_line_range(target, allow_single_line=True)
                if (
                    role == "剃刀复审"
                    and feature_data is not None
                    and target != feature_id
                    and (target_range is None or target_range[0] not in keep_files)
                ):
                    problems.append(
                        f"{finding_label}.target 复审发现目标必须在本功能要逐行覆盖的文件里或是本功能 id：{target}"
                    )
                    continue
                if target_range is not None:
                    finding_ranges.append((finding, target_range))
                elif target == feature_id:
                    feature_targets.append(finding)

            if role == "剃刀" and feature_data is not None:
                problems.extend(_validate_smallest(
                    review,
                    feature_label,
                    feature_id,
                    feature_data,
                    findings,
                ))

            if (role == "剃刀复审" and isinstance(feature_id, str)
                    and feature_data is not None):
                pending_recheck_verdicts.append((
                    feature_id,
                    review.get("verdicts"),
                    f"{feature_label} 的功能 {feature_id}",
                    finding_ranges,
                    feature_targets,
                ))

            if role == "剃刀" and feature_data is not None:
                problems.extend(_validate_keep_items(
                    review.get("keep"),
                    f"报告 {label} 的功能 {feature_id}.keep",
                    repository,
                    feature_data,
                    keep_files,
                    finding_ranges,
                    feature_targets,
                ))
                valid_reduction_targets = {
                    id(finding)
                    for finding, target_range in finding_ranges
                    if target_range[0] in keep_files
                }
                valid_reduction_targets.update(id(finding) for finding in feature_targets)
                has_reduction = any(
                    isinstance(finding, dict)
                    and finding.get("kind") in {"删除", "合并"}
                    and id(finding) in valid_reduction_targets
                    for finding in findings
                )
                if (
                    feature_id in expected_features
                    and feature_data["why"]["source"] != "起草"
                    and keep_files
                    and not has_reduction
                ):
                    # Zero reductions need an independent check; a numerical quota would reward made-up findings.
                    required_rechecks.add(feature_id)

    # Requiring one verdict per keep prevents reviewers from skipping items and returning an empty report.
    location_patterns = (
        _tracked_location_patterns(tracked_files) if pending_recheck_verdicts else []
    )
    for feature_id, verdicts, label, finding_ranges, feature_targets in pending_recheck_verdicts:
        problems.extend(_validate_recheck_verdicts(
            verdicts,
            label,
            razor_keep_lines.get(feature_id, []),
            finding_ranges,
            feature_targets,
            repository,
            tracked_files,
            evidence_lines_by_file,
            location_patterns,
        ))

    for role in sorted(ROLES):
        for feature_id in expected_features:
            count = counts[role].get(feature_id, 0)
            if count == 0:
                problems.append(f"{role}报告缺少功能 {feature_id}")
            elif count > 1:
                problems.append(f"{role}报告重复功能 {feature_id} {count} 次")
    for feature_id in sorted(required_rechecks):
        count = recheck_counts.get(feature_id, 0)
        if count == 0:
            problems.append(f"缺剃刀复审：{feature_id}")
        elif count > 1:
            problems.append(f"剃刀复审重复功能 {feature_id} {count} 次")
    for feature_id in sorted(recheck_counts):
        if feature_id not in required_rechecks:
            problems.append(f"不用复审：{feature_id}")
    if recheck_report_seen and not required_rechecks and not recheck_counts:
        problems.append("不用复审：没有需要复审的功能")
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
