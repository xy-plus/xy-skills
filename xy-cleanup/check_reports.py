import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "xy-repo-map"))
sys.path.insert(0, str(REPOSITORY / "xy-review"))
from repo_map_lib.git_repository import ArgumentError  # noqa: E402
from repo_map_lib.git_repository import tracked_paths  # noqa: E402
from repo_map_lib.map_data import (  # noqa: E402
    build_map,
    file_category_with_skip,
)
from repo_map_lib.metrics import count_non_whitespace_characters  # noqa: E402
from repo_map_lib.skip_paths import is_skipped_path, read_skip_paths  # noqa: E402
from approvals import (  # noqa: E402
    APPROVED_RELATIVE_PATH,
    feature_fingerprints,
    feature_values,
    read_approvals,
    review_features,
    validate_scope,
)
from check_handoff import object_without_duplicate_keys  # noqa: E402
from duplicate_blocks import TODO_FILENAME, find_duplicate_windows, other_ranges_for_keep  # noqa: E402
from rule_references import basis_is_supported  # noqa: E402


REPORT_KEYS = {"role", "repo", "features"}
FEATURE_KEYS = {
    "主审": {
        "feature", "what_true", "why_holds", "structure", "todo_items", "findings",
    },
    "剃刀": {
        "feature", "necessity", "smallest", "rewrites", "usage_candidates", "keep",
        "todo_items", "findings",
    },
    "剃刀复审": {"feature", "verdicts", "findings"},
}
FINDING_KEYS = {
    "kind", "target", "problem", "proposal", "changes_behavior", "decision",
    "basis", "evidence", "benefit", "cost",
}
BASIS_KEYS = {"source", "text"}
TODO_ITEM_KEYS = {"lines", "needed", "smaller", "why_holds"}
KEEP_ITEM_KEYS = {"lines", "requirement", "achieved", "cost"}
REWRITE_KEYS = {"lines", "text"}
RECHECK_VERDICT_KEYS = {"lines", "attempt", "evidence", "verdict"}
CARRIED_KEYS = {"old", "verdict", "target", "evidence"}
TEST_CANDIDATE_KEYS = {
    "runs", "unmeasured_files", "generated_mutants", "mutants", "functions",
    "unmeasured_functions", "candidates",
}
TEST_RUN_KEYS = {
    "pytest": {"runner", "root", "command", "junit", "list", "list_log"},
    "nextest": {"runner", "root", "command", "junit", "list"},
}
ROLES = {"主审", "剃刀"}
REPORT_ROLES = ROLES | {"剃刀复审"}
KINDS = {"删除", "合并", "改名", "挪位置", "拆分", "重构", "修根因", "加约束", "改清单", "孤儿", "其他"}
DECISIONS = {"直接做", "问你"}
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
    result.add_argument("--usage-coverage", required=True, help="usage-coverage.json 路径")
    result.add_argument(
        "--carry-over", required=True, nargs="+",
        help="上一轮未实施且未由用户决定的报告路径，或单独写 无",
    )
    result.add_argument("--test-candidates", required=True, help="test-candidates.json 路径")
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


def _paired_delimiter_fragments(text, marker, minimum_length):
    fragments = []
    index = 0
    while index < len(text):
        if text[index] != marker or (index > 0 and text[index - 1] == marker):
            index += 1
            continue
        delimiter_end = index + 1
        while delimiter_end < len(text) and text[delimiter_end] == marker:
            delimiter_end += 1
        if delimiter_end - index < minimum_length:
            index = delimiter_end
            continue
        delimiter = text[index:delimiter_end]
        search_from = delimiter_end
        closing = None
        # A same-length backtick run inside a code block can close early and hide later code.
        # Indented code blocks have no fence markers, so this check does not inspect them.
        while True:
            candidate = text.find(delimiter, search_from)
            if candidate < 0:
                break
            candidate_end = candidate + len(delimiter)
            if (
                (candidate == 0 or text[candidate - 1] != marker)
                and (candidate_end == len(text) or text[candidate_end] != marker)
            ):
                closing = candidate_end
                break
            search_from = candidate_end
        if closing is None:
            index = delimiter_end
            continue
        fragments.append((index, closing, text[index:closing]))
        index = closing
    return fragments


def _markdown_cells(line):
    line = line.strip()
    cells = []
    separators = []
    start = 0
    for index, character in enumerate(line):
        if character != "|":
            continue
        backslashes = 0
        previous = index - 1
        while previous >= 0 and line[previous] == "\\":
            backslashes += 1
            previous -= 1
        # An odd backslash run escapes this bar, so it stays in its cell.
        if backslashes % 2:
            continue
        separators.append(index)
        cells.append(line[start:index])
        start = index + 1
    if not separators:
        return None
    cells.append(line[start:])
    if separators[0] == 0:
        cells.pop(0)
    if separators[-1] == len(line) - 1:
        cells.pop()
    if not cells:
        cells = [""]
    return [cell.strip() for cell in cells]


def _markdown_tables(text):
    lines = text.splitlines()
    code_lines = _markdown_code_lines(lines, set())

    tables = []
    index = 1
    while index < len(lines):
        if index in code_lines or index - 1 in code_lines:
            index += 1
            continue
        divider_cells = _markdown_cells(lines[index])
        header_cells = _markdown_cells(lines[index - 1])
        if (divider_cells is None or header_cells is None
                or not all(re.fullmatch(r":?-+:?", cell) for cell in divider_cells)):
            index += 1
            continue

        rows = [(index, header_cells), (index + 1, divider_cells)]
        next_line = index + 1
        while next_line < len(lines):
            if next_line in code_lines or not lines[next_line].strip():
                break
            cells = _markdown_cells(lines[next_line])
            if cells is None:
                break
            rows.append((next_line + 1, cells))
            next_line += 1
        tables.append({"start": index, "end": next_line, "rows": rows})
        index = next_line
    return tables


def _validate_rewrite_table_structure(path, original_text, blocks, label):
    problems = []
    original_rows_by_block = {index: [] for index in range(len(blocks))}
    for table in _markdown_tables(original_text):
        # Map original table rows by line so a table split between rewrite blocks is rejected.
        row_blocks = [
            next((index for index, (line_range, _block_label, _text) in enumerate(blocks)
                  if line_range[1] <= line_number <= line_range[2]), None)
            for line_number, _cells in table["rows"]
        ]
        block_indexes = set(row_blocks)
        if len(block_indexes) > 1:
            block_ranges = []
            for block_index in row_blocks:
                if block_index is None:
                    description = "未覆盖"
                else:
                    _range, _block_label, _text = blocks[block_index]
                    description = f"{path}:{_range[1]}-{_range[2]}"
                if description not in block_ranges:
                    block_ranges.append(description)
            problems.append(
                f"{label} 文件 {path} 的 Markdown 表格 {path}:{table['start']}-{table['end']} "
                f"跨多个改写块：{', '.join(block_ranges)}"
            )
            continue
        if None in block_indexes or not row_blocks:
            continue
        block_index = row_blocks[0]
        if blocks[block_index][2] != "":
            original_rows_by_block[block_index].extend(table["rows"])

    for block_index, (line_range, _block_label, rewritten_text) in enumerate(blocks):
        if rewritten_text == "":
            continue
        expected_rows = original_rows_by_block[block_index]
        rewritten_rows = [
            row for table in _markdown_tables(rewritten_text) for row in table["rows"]
        ]
        for row_index in range(max(len(expected_rows), len(rewritten_rows))):
            if row_index >= len(expected_rows):
                location = f"改写文本第 {rewritten_rows[row_index][0]} 行多出表格行"
                difference = f"原文 {len(expected_rows)} 行、改写稿 {len(rewritten_rows)} 行"
            elif row_index >= len(rewritten_rows):
                location = f"原文第 {expected_rows[row_index][0]} 行在改写稿中缺失"
                difference = f"原文 {len(expected_rows)} 行、改写稿 {len(rewritten_rows)} 行"
            else:
                original_line, original_cells = expected_rows[row_index]
                rewritten_line, rewritten_cells = rewritten_rows[row_index]
                if (len(original_cells) == len(rewritten_cells)
                        and original_cells[0] == rewritten_cells[0]):
                    continue
                location = f"原文第 {original_line} 行、改写文本第 {rewritten_line} 行"
                difference = (
                    f"列数 {len(original_cells)}→{len(rewritten_cells)}，"
                    f"首格 {original_cells[0]!r}→{rewritten_cells[0]!r}"
                )
            block_path, block_start, block_end = line_range
            problems.append(
                f"{label} 文件 {path} 改写块 {block_path}:{block_start}-{block_end} "
                f"Markdown 表格结构不一致：{location}（{difference}）"
            )
            break
    return problems


def _validate_empty_feature_items(data, expected_features):
    problems = []
    for feature_id in expected_features:
        feature = data["features"].get(feature_id)
        if feature is None or feature.get("items") != []:
            continue
        has_direct_files = any(
            any(group_paths) for group_paths in feature["files"].values()
        )
        # A fileless parent only groups children; leaf items still describe its own goal.
        if feature.get("children") and not has_direct_files:
            continue
        problems.append(f"功能 {feature_id} 条目为空，须先起草")
    return problems


def _validate_rewrites(rewrites, label, repository, document_files, findings):
    problems = []
    if not isinstance(rewrites, list):
        return [f"{label} 必须是数组"]

    lines_by_file = {}
    ranges_by_file = {}
    texts_by_file = {}
    labels_by_file = {}
    blocks_by_file = {}
    last_start_by_file = {}
    for index, rewrite in enumerate(rewrites):
        rewrite_label = f"{label}[{index}]"
        item_problems = _field_errors(rewrite, REWRITE_KEYS, rewrite_label)
        problems.extend(item_problems)
        if item_problems or not isinstance(rewrite, dict):
            continue
        line_spec = rewrite["lines"]
        text = rewrite["text"]
        if not isinstance(text, str):
            problems.append(f"{rewrite_label}.text 必须是字符串")
            continue
        line_range, range_problems = _checked_line_range(
            line_spec,
            rewrite_label,
            "lines",
            repository,
            document_files,
            lines_by_file,
            allow_single_line=False,
        )
        problems.extend(range_problems)
        if line_range is None:
            continue
        overlapping = _record_line_range(
            ranges_by_file, line_range, rewrite_label, "lines",
        )
        if overlapping is not None:
            problems.append(overlapping)
        path, start_line, end_line = line_range
        blocks_by_file.setdefault(path, []).append((line_range, rewrite_label, text))
        previous_start = last_start_by_file.get(path)
        if previous_start is not None and start_line < previous_start:
            problems.append(
                f"{rewrite_label}.lines 必须按同一文件起始行递增：{path}:{start_line}-{end_line}"
            )
        last_start_by_file[path] = start_line
        labels_by_file.setdefault(path, rewrite_label)
        texts_by_file.setdefault(path, []).append(text)

    original_text_by_file = {}
    for path in document_files:
        try:
            original_text = (Path(repository) / path).read_bytes().decode(
                "utf-8", errors="replace",
            )
        except OSError as error:
            problems.append(f"{label} 无法读取原文 {path}：{error}")
            continue
        original_text_by_file[path] = original_text
        rewritten_texts = texts_by_file.get(path, [])
        rewritten_text = "\n".join(rewritten_texts)
        block_ranges = ranges_by_file.get(path, [])
        required_fragments = {}
        fragments = (
            _paired_delimiter_fragments(original_text, "`", 1)
            + _paired_delimiter_fragments(original_text, "~", 3)
        )
        for start, end, fragment in sorted(fragments, key=lambda item: item[0]):
            start_line = len(re.findall(r"\r\n|\r|\n", original_text[:start])) + 1
            end_line = len(re.findall(r"\r\n|\r|\n", original_text[:end - 1])) + 1
            deleted_whole = any(
                text == "" and block_start <= start_line and end_line <= block_end
                for (block_start, block_end), text in zip(block_ranges, rewritten_texts)
            )
            if not deleted_whole:
                required_fragments.setdefault(fragment, None)
        for fragment in required_fragments:
            if fragment not in rewritten_text:
                problems.append(
                    f"{label} 文件 {path} 的改写全文缺少原文反引号片段：{fragment}"
                )
        problems.extend(_validate_rewrite_table_structure(
            path, original_text, blocks_by_file.get(path, []), label,
        ))

    problems.extend(_uncovered_nonempty_lines(
        document_files,
        ranges_by_file,
        label,
        "直属文档改写",
        repository,
        lines_by_file,
    ))

    for path, texts in texts_by_file.items():
        rewrite_label = labels_by_file[path]
        original_text = original_text_by_file.get(path)
        if original_text is None:
            continue
        before_chars = count_non_whitespace_characters(original_text)
        after_chars = sum(count_non_whitespace_characters(text) for text in texts)
        if after_chars >= before_chars:
            continue

        line_count = len(original_text.splitlines())
        target = f"{path}:1-{line_count}"
        expected_kind = "删除" if all(text == "" for text in texts) else "重构"
        matching = [
            finding for finding in findings
            if isinstance(finding, dict) and finding.get("target") == target
        ]
        matching_kinds = {
            finding.get("kind") for finding in matching
            if isinstance(finding.get("kind"), str)
        }
        if expected_kind not in matching_kinds:
            if matching:
                problems.append(
                    f"{rewrite_label} target {target} 的发现类型必须是 {expected_kind}；"
                    f"原文 {before_chars} 字，改写后 {after_chars} 字"
                )
            else:
                problems.append(
                    f"{rewrite_label} 比原文短，必须有 target 为 {target}、kind 为 {expected_kind} 的发现；"
                    f"原文 {before_chars} 字，改写后 {after_chars} 字"
                )
            continue

    return problems


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


def _record_razor_keep_reasons(keep_items, label, feature_id, seen_reasons):
    problems = []
    if not isinstance(keep_items, list):
        return problems
    for item in keep_items:
        if not isinstance(item, dict):
            continue
        lines = item.get("lines")
        for field in ("achieved", "cost"):
            answer = item.get(field)
            if not isinstance(answer, str) or not answer.strip():
                continue
            key = (field, answer.strip())
            previous = seen_reasons.get(key)
            if previous is None:
                seen_reasons[key] = (label, feature_id, lines)
                continue
            previous_label, previous_feature, previous_lines = previous
            problems.append(
                f"剃刀 keep.{field} 逐字相同：报告 {previous_label} 的功能 {previous_feature} "
                f"lines={previous_lines}；报告 {label} 的功能 {feature_id} lines={lines}"
            )
    return problems


def _parse_line_range(value, allow_single_line):
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
                        lines_by_file, allow_single_line):
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
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            if line.strip() and line_number not in covered_lines:
                problems.append(
                    f"{label} 未覆盖{description}的非空行：{path}:{line_number}"
                )
    return problems


def _validate_feature_todo_reduction(feature_id, feature_targets, finding_ranges,
                                     direct_todo_files, label, repository):
    if not direct_todo_files or not any(
        finding.get("kind") in {"删除", "合并"}
        for finding in feature_targets
    ):
        return []

    # A feature-level reduction affects every direct TODO file, so each nonempty line needs its own user question.
    todo_ranges = {}
    for _finding, target_range in finding_ranges:
        path, start_line, end_line = target_range
        if path in direct_todo_files:
            todo_ranges.setdefault(path, []).append((start_line, end_line))
    return _uncovered_nonempty_lines(
        direct_todo_files,
        todo_ranges,
        label,
        f"功能级删除或合并 {feature_id} 影响的直属 TODO ",
        repository,
        {},
    )


def _validate_finding(finding, label, tracked_todo_files,
                      feature_id, direct_todo_files):
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
        target_range = _parse_line_range(finding["target"], allow_single_line=True)
        target_is_todo = target_range is not None and target_range[0] in tracked_todo_files
        target_is_feature_todo_reduction = (
            finding["target"] == feature_id
            and bool(direct_todo_files)
            and kind in {"删除", "合并"}
        )
        if target_is_todo:
            # TODO 文件的改动一律由用户决定，即使删除或合并不改变行为。
            if decision != "问你":
                problems.append(
                    f"{label}.decision 目标在 TODO 文件 {finding['target']} 时必须是 问你"
                )
        elif target_is_feature_todo_reduction:
            if decision != "问你":
                problems.append(
                    f"{label}.decision 功能级删除或合并会影响直属 TODO，必须是 问你"
                )
        else:
            # Behavior-preserving internal changes are authorized directly; public usage changes and listed exceptions remain user decisions.
            must_do_directly = not changes_behavior and kind not in {"孤儿", "改清单"}
            if (decision == "直接做") != must_do_directly:
                problems.append(f"{label}.decision 行为不变且非孤儿或改清单的改动必须是 直接做；其余必须是 问你")

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


def _usage_source_ranges(repository):
    readme_path = Path(repository) / "README.md"
    try:
        text = readme_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return [], [f"无法读取 README.md 的「实际使用」来源：{error}"]

    lines = text.splitlines()
    code_lines = _markdown_code_lines(lines, set())
    headings = []
    for index, line in enumerate(lines):
        if index in code_lines:
            continue
        heading = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+|$)(.*)$", line)
        if heading is not None:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2)).strip()
            headings.append((index, len(heading.group(1)), title, index))
    for index in range(len(lines) - 1):
        if index in code_lines or index + 1 in code_lines:
            continue
        title = lines[index].strip()
        underline = lines[index + 1]
        if (not title or MARKDOWN_HEADING.match(lines[index])
                or not SETEXT_UNDERLINE.fullmatch(underline)):
            continue
        level = 1 if underline.lstrip().startswith("=") else 2
        headings.append((index, level, title, index + 1))
    headings.sort(key=lambda item: item[0])

    section = next(
        (heading for heading in headings if "实际使用" in heading[2]),
        None,
    )
    if section is None:
        return [], ["README.md 缺少「实际使用」一节"]
    heading_index, heading_level, _title, heading_end = section
    section_end = next(
        (index for index, level, _title, _end in headings
         if index > heading_index and level <= heading_level),
        len(lines),
    )
    body_start = heading_end + 1

    # A table row, list item, or paragraph is one declared source; table rows take
    # precedence because each row in the current README names a distinct record.
    tables = []
    for table in _markdown_tables(text):
        table_header = table["start"] - 1
        if body_start <= table_header and table["end"] <= section_end:
            tables.append(table)
    if tables:
        return [
            f"README.md:{line_number}-{line_number}"
            for table in tables
            for line_number, _cells in table["rows"][2:]
        ], []

    list_items = []
    for index in range(body_start, section_end):
        if index in code_lines:
            continue
        if re.match(r"^ {0,3}(?:[-+*]|[0-9]{1,9}[.)])(?:[ \t]+|$)", lines[index]):
            list_items.append(index)
    if list_items:
        sources = []
        for item_index, start in enumerate(list_items):
            following = list_items[item_index + 1] if item_index + 1 < len(list_items) else section_end
            end = start
            for index in range(start + 1, following):
                if (index in code_lines or not lines[index].strip()
                        or MARKDOWN_HEADING.match(lines[index])):
                    break
                end = index
            sources.append(f"README.md:{start + 1}-{end + 1}")
        return sources, []

    sources = []
    paragraph_start = None
    paragraph_end = None
    for index in range(body_start, section_end):
        line = lines[index]
        structural = (
            index in code_lines
            or not line.strip()
            or MARKDOWN_HEADING.match(line)
        )
        if structural:
            if paragraph_start is not None:
                sources.append(f"README.md:{paragraph_start + 1}-{paragraph_end + 1}")
                paragraph_start = paragraph_end = None
            continue
        if paragraph_start is None:
            paragraph_start = index
        paragraph_end = index
    if paragraph_start is not None:
        sources.append(f"README.md:{paragraph_start + 1}-{paragraph_end + 1}")
    return sources, []


def _usage_line_range(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)", value)
    if match is None:
        return None
    start_line, end_line = (int(part) for part in match.groups())
    if start_line > end_line:
        return None
    return start_line, end_line


def _direct_code_files(data, feature_ids, repository, tracked_files):
    skip_paths = read_skip_paths(repository, tracked_files)
    code_files = set()
    for feature_id in feature_ids:
        feature_data = data["features"].get(feature_id)
        if feature_data is None:
            continue
        for paths in feature_data["files"].values():
            code_files.update(
                path for path in paths
                if file_category_with_skip(repository, path, skip_paths) == "code"
            )
    return code_files


def _validate_usage_coverage(coverage, data, repository, feature_ids, tracked_files):
    label = "usage-coverage"
    problems = _field_errors(coverage, {"sources", "files", "unmeasured"}, label)
    if not isinstance(coverage, dict):
        return problems

    expected_sources, source_problems = _usage_source_ranges(repository)
    problems.extend(source_problems)
    sources = coverage.get("sources")
    if not isinstance(sources, list):
        problems.append(f"{label}.sources 必须是数组")
        sources = []
    if len(sources) < len(expected_sources):
        problems.append(
            f"{label}.sources 缺少 README.md「实际使用」来源：应有 {len(expected_sources)} 条，实有 {len(sources)} 条"
        )
    elif len(sources) > len(expected_sources):
        problems.append(
            f"{label}.sources 多出 README.md「实际使用」来源：应有 {len(expected_sources)} 条，实有 {len(sources)} 条"
        )

    any_scenario_ran = False
    for index, source in enumerate(sources):
        source_label = f"{label}.sources[{index}]"
        item_problems = _field_errors(
            source, {"source", "scenarios", "why_none"}, source_label,
        )
        problems.extend(item_problems)
        if item_problems or not isinstance(source, dict):
            continue
        source_value = source.get("source")
        if not isinstance(source_value, str):
            problems.append(f"{source_label}.source 必须是字符串")
        elif index >= len(expected_sources):
            problems.append(f"{source_label}.source 没有对应的 README.md 来源：{source_value}")
        elif source_value != expected_sources[index]:
            problems.append(
                f"{source_label}.source 必须恰好对应 {expected_sources[index]}，实际为 {source_value}"
            )

        scenarios = source.get("scenarios")
        if not isinstance(scenarios, list):
            problems.append(f"{source_label}.scenarios 必须是数组")
            scenarios = []
        why_none = source.get("why_none")
        if not isinstance(why_none, str):
            problems.append(f"{source_label}.why_none 必须是字符串")
        elif not scenarios and not why_none.strip():
            problems.append(f"{source_label} 的 scenarios 为空时 why_none 必须说明原因")
        elif scenarios and why_none != "":
            problems.append(f"{source_label} 的 scenarios 非空时 why_none 必须为空字符串")

        for scenario_index, scenario in enumerate(scenarios):
            scenario_label = f"{source_label}.scenarios[{scenario_index}]"
            scenario_problems = _field_errors(
                scenario, {"command", "ran", "why_not"}, scenario_label,
            )
            problems.extend(scenario_problems)
            if scenario_problems or not isinstance(scenario, dict):
                continue
            if not _nonempty(scenario.get("command")):
                problems.append(f"{scenario_label}.command 必须是非空字符串")
            ran = scenario.get("ran")
            why_not = scenario.get("why_not")
            if type(ran) is not bool:
                problems.append(f"{scenario_label}.ran 必须是布尔值")
            elif ran:
                any_scenario_ran = True
                if why_not != "":
                    problems.append(f"{scenario_label}.ran 为 true 时 why_not 必须为空字符串")
            elif not _nonempty(why_not):
                problems.append(f"{scenario_label}.ran 为 false 时 why_not 必须说明原因")

    code_files = _direct_code_files(data, feature_ids, repository, tracked_files)
    files = coverage.get("files")
    if not isinstance(files, dict):
        problems.append(f"{label}.files 必须是对象")
        files = {}
    if not any_scenario_ran and files:
        # Without a successful run there is no observation that can establish missed lines.
        problems.append(f"没有 ran 为 true 的场景时 files 必须是空对象")

    for path, file_entry in files.items():
        file_label = f"{label}.files[{path}]"
        if path not in code_files:
            problems.append(f"{file_label} 不属于本次审查范围内的直属代码文件")
        entry_problems = _field_errors(file_entry, {"missed"}, file_label)
        problems.extend(entry_problems)
        if entry_problems or not isinstance(file_entry, dict):
            continue
        missed = file_entry.get("missed")
        if not isinstance(missed, list):
            problems.append(f"{file_label}.missed 必须是数组")
            continue
        file_lines = (
            _read_file_lines(Path(repository) / path) if path in code_files else None
        )
        previous_end = None
        for range_index, line_range in enumerate(missed):
            range_label = f"{file_label}.missed[{range_index}]"
            parsed = _usage_line_range(line_range)
            if parsed is None:
                problems.append(f"{range_label} 必须写成有效的 起始行-结束行：{line_range!r}")
                continue
            start_line, end_line = parsed
            if file_lines is None or end_line > len(file_lines):
                count = 0 if file_lines is None else len(file_lines)
                problems.append(f"{range_label} 超出文件范围：{path}:{line_range}（共 {count} 行）")
                continue
            if previous_end is not None and start_line <= previous_end + 1:
                problems.append(
                    f"{range_label} 必须按行序排列，且相邻的 missed 行应合并成一个范围"
                )
            previous_end = end_line

    unmeasured = coverage.get("unmeasured")
    if not isinstance(unmeasured, list):
        problems.append(f"{label}.unmeasured 必须是数组")
        unmeasured = []
    unmeasured_paths = []
    for index, item in enumerate(unmeasured):
        item_label = f"{label}.unmeasured[{index}]"
        item_problems = _field_errors(item, {"path", "why"}, item_label)
        problems.extend(item_problems)
        if item_problems or not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path:
            problems.append(f"{item_label}.path 必须是非空字符串")
            continue
        if path not in code_files:
            problems.append(f"{item_label}.path 不属于本次审查范围内的直属代码文件：{path}")
        if not _nonempty(item.get("why")):
            problems.append(f"{item_label}.why 必须是非空字符串")
        unmeasured_paths.append(path)
    for path in sorted(set(unmeasured_paths)):
        if unmeasured_paths.count(path) > 1:
            problems.append(f"{label}.unmeasured 重复文件：{path}")

    measured_paths = set(files)
    unmeasured_set = set(unmeasured_paths)
    for path in sorted(code_files):
        count = (path in measured_paths) + (path in unmeasured_set)
        if count != 1:
            problems.append(
                f"直属代码文件必须恰好出现在 files 或 unmeasured：{path}"
            )
    return problems


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


def _validate_usage_candidates(items, label, feature_id, direct_code_files,
                               usage_files, finding_ranges, feature_targets,
                               repository, tracked_files, lines_by_file,
                               location_patterns):
    if not isinstance(items, list):
        return [f"{label}.usage_candidates 必须是数组"]

    problems = []
    expected_ranges = set()
    for path in direct_code_files:
        entry = usage_files.get(path)
        missed = entry.get("missed") if isinstance(entry, dict) else None
        if not isinstance(missed, list):
            continue
        for line_range in missed:
            if _usage_line_range(line_range) is not None:
                expected_ranges.add(f"{path}:{line_range}")

    seen_ranges = {}
    for index, item in enumerate(items):
        item_label = f"{label}.usage_candidates[{index}]"
        item_problems = _field_errors(
            item, {"lines", "verdict", "evidence"}, item_label,
        )
        problems.extend(item_problems)
        if item_problems or not isinstance(item, dict):
            continue
        lines = item.get("lines")
        if not isinstance(lines, str) or not _nonempty(lines):
            problems.append(f"{item_label}.lines 必须是非空字符串")
            continue
        seen_ranges[lines] = seen_ranges.get(lines, 0) + 1
        if lines not in expected_ranges:
            problems.append(
                f"{label}.usage_candidates 多出不在 usage-coverage missed 里的候选：{lines}"
            )

        verdict = item.get("verdict")
        if not isinstance(verdict, str) or verdict not in {"用到", "删"}:
            problems.append(f"{item_label}.verdict 只能是 用到 或 删")
        candidate_range = _parse_line_range(lines, allow_single_line=False)
        evidence = item.get("evidence")
        if not isinstance(evidence, str):
            problems.append(f"{item_label}.evidence 必须是字符串")
        elif verdict == "用到":
            if (not _nonempty(evidence) or candidate_range is None
                    or not _has_external_evidence_location(
                        evidence,
                        candidate_range,
                        item_label,
                        repository,
                        tracked_files,
                        lines_by_file,
                        location_patterns,
                    )):
                problems.append(
                    f"{item_label}.evidence 必须引至少一处有效位置（本候选行范围以外）：{lines}"
                )
        elif verdict == "删" and candidate_range is not None:
            path, start_line, end_line = candidate_range
            overlaps_finding = any(
                path == finding_range[0]
                and start_line <= finding_range[2]
                and finding_range[1] <= end_line
                for _finding, finding_range in finding_ranges
            )
            feature_reduction = any(
                finding.get("kind") in {"删除", "合并"}
                for finding in feature_targets
            )
            if not overlaps_finding and not feature_reduction:
                problems.append(
                    f"{item_label} 判删须有重叠发现或功能级删除、合并发现：{lines}"
                )

    for lines in sorted(expected_ranges):
        count = seen_ranges.get(lines, 0)
        if count == 0:
            problems.append(f"{label}.usage_candidates 缺少实际使用候选：{lines}")
        elif count > 1:
            problems.append(
                f"{label}.usage_candidates 的候选必须恰有一条：{lines}（实际 {count} 条）"
            )
    return problems


def _validate_orphan_nonstanding(lines, label, repository, usage_coverage,
                                 skip_paths):
    line_range = _parse_line_range(lines, allow_single_line=False)
    if line_range is None:
        return [f"{label} 孤儿 verdict.lines 必须是有效文件范围：{lines}"]
    path, start_line, end_line = line_range
    category = file_category_with_skip(repository, path, skip_paths)
    if category in {"tests", "docs"}:
        return []
    if category != "code":
        return [f"{label} 孤儿目标不是代码、测试或文档文件，不能判站不住：{path}"]

    files = usage_coverage.get("files", {}) if isinstance(usage_coverage, dict) else {}
    unmeasured = usage_coverage.get("unmeasured", []) if isinstance(usage_coverage, dict) else []
    reason = next((
        item.get("why") for item in unmeasured
        if isinstance(item, dict) and item.get("path") == path
    ), None) if isinstance(unmeasured, list) else None
    if reason is not None:
        return [
            f"{label} 孤儿目标所在代码文件 {path} 在 usage-coverage.unmeasured 中，"
            f"不能判站不住（{reason}）"
        ]
    if not isinstance(files, dict) or path not in files:
        return [
            f"{label} 孤儿目标所在代码文件未列入 usage-coverage.files，不能判站不住：{path}"
        ]
    entry = files[path]
    missed = entry.get("missed") if isinstance(entry, dict) else None
    if not isinstance(missed, list):
        return [
            f"{label} 孤儿目标所在代码文件缺少有效的 usage-coverage.files[{path}].missed"
        ]
    # README actual-use scenarios give executed lines practical value; unmeasured code cannot prove absence of use.
    missed_ranges = [
        parsed for item in missed
        if (parsed := _usage_line_range(item)) is not None
    ]
    executed_lines = [
        line for line in range(start_line, end_line + 1)
        if not any(start <= line <= end for start, end in missed_ranges)
    ]
    return [
        f"{label} 孤儿目标站不住的行必须全在 usage-coverage 的 missed 范围内；"
        f"{path}:{line} 未落在 usage-coverage 的 missed 范围"
        for line in executed_lines
    ]


def _validate_recheck_verdicts(verdicts, label, keep_lines, orphan_lines,
                               finding_ranges, feature_targets, repository,
                               tracked_files, lines_by_file, location_patterns,
                               usage_coverage, skip_paths):
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
            problems.append(
                f"{verdict_label}.lines 必须与首轮 keep 或孤儿目标的范围完全相同的字符串"
            )
        else:
            verdict_counts[lines] = verdict_counts.get(lines, 0) + 1
        if not _nonempty(verdict["attempt"]):
            problems.append(f"{verdict_label}.attempt 必须是非空字符串")
        evidence = verdict["evidence"]
        if not _nonempty(evidence):
            problems.append(f"{verdict_label}.evidence 必须是非空字符串")
        elif isinstance(lines, str):
            line_range = _parse_line_range(lines, allow_single_line=False)
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
    orphan_counts = {}
    for lines in orphan_lines:
        orphan_counts[lines] = orphan_counts.get(lines, 0) + 1
    for lines in keep_counts.keys() | orphan_counts.keys():
        actual_count = verdict_counts.get(lines, 0)
        keep_count = keep_counts.get(lines, 0)
        orphan_count = orphan_counts.get(lines, 0)
        if actual_count < keep_count:
            problems.append(
                f"{label}.verdicts 缺少 keep 的判定：{lines}（应有 {keep_count} 条，实有 {actual_count} 条）"
            )
        if orphan_count:
            if actual_count < keep_count + orphan_count:
                problems.append(
                    f"{label}.verdicts 缺少孤儿目标的判定：{lines}"
                )
            elif actual_count > keep_count + orphan_count:
                problems.append(f"{label}.verdicts 重复判定孤儿目标：{lines}")
        elif actual_count > keep_count:
            problems.append(f"{label}.verdicts 重复判定 keep：{lines}")
    for lines in verdict_counts.keys() - (keep_counts.keys() | orphan_counts.keys()):
        problems.append(f"{label}.verdicts 多出不在 keep 或孤儿目标里的判定：{lines}")

    for lines, verdict_label in nonstanding:
        if lines in orphan_counts:
            # An orphan discovery is itself the finding; usage evidence, not overlap, decides deletion.
            problems.extend(_validate_orphan_nonstanding(
                lines, verdict_label, repository, usage_coverage, skip_paths,
            ))
            continue
        verdict_range = _parse_line_range(lines, allow_single_line=False)
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


def _read_carry_over_findings(report_paths, expected_features):
    findings_by_feature = {}
    problems = []
    seen_references = set()
    for report_path in report_paths:
        try:
            document = json.loads(
                Path(report_path).read_text(encoding="utf-8"),
                object_pairs_hook=object_without_duplicate_keys,
            )
        except (OSError, UnicodeError, ValueError) as error:
            problems.append(f"无法读取 carry-over 报告 {report_path}：{error}")
            continue
        label = f"carry-over 报告 {report_path}"
        if not isinstance(document, dict) or not isinstance(document.get("features"), list):
            problems.append(f"{label} 必须是含 features 数组的对象")
            continue
        for feature_index, review in enumerate(document["features"]):
            feature_label = f"{label} 的 features[{feature_index}]"
            if not isinstance(review, dict):
                problems.append(f"{feature_label}必须是对象")
                continue
            feature_id = review.get("feature")
            if feature_id not in expected_features:
                continue
            findings = review.get("findings")
            if not isinstance(findings, list):
                problems.append(f"{feature_label}.findings 必须是数组")
                continue
            for finding_index, finding in enumerate(findings, start=1):
                if not isinstance(finding, dict) or not _nonempty(finding.get("target")):
                    problems.append(
                        f"{feature_label}.findings[{finding_index - 1}] 必须有非空 target"
                    )
                    continue
                reference = f"{report_path}#{feature_id}#{finding_index}"
                if reference in seen_references:
                    problems.append(f"carry-over 报告重复列出旧发现：{reference}")
                    continue
                seen_references.add(reference)
                findings_by_feature.setdefault(feature_id, []).append({"old": reference})
    return findings_by_feature, problems


def _validate_carried(items, label, previous_findings, recheck_findings):
    if not isinstance(items, list):
        return [f"{label}.carried 必须是数组"]

    problems = []
    previous_by_reference = {item["old"]: item for item in previous_findings}
    counts = {}
    current_targets = {
        finding.get("target")
        for finding in recheck_findings
        if isinstance(finding, dict) and isinstance(finding.get("target"), str)
    }
    for index, item in enumerate(items):
        item_label = f"{label}.carried[{index}]"
        item_problems = _field_errors(item, CARRIED_KEYS, item_label)
        problems.extend(item_problems)
        if item_problems or not isinstance(item, dict):
            continue
        old = item["old"]
        if not isinstance(old, str) or old not in previous_by_reference:
            problems.append(f"{item_label}.old 对不上上一轮发现：{old!r}")
        else:
            counts[old] = counts.get(old, 0) + 1
            if counts[old] > 1:
                problems.append(f"{label}.carried 重复上一轮发现：{old}")

        verdict = item["verdict"]
        if not isinstance(verdict, str) or verdict not in {"仍成立", "不成立"}:
            problems.append(f"{item_label}.verdict 只能是 仍成立 或 不成立")
        target = item["target"]
        if verdict == "仍成立":
            if not _nonempty(target):
                problems.append(f"{item_label}.target 在 verdict 为 仍成立 时必须非空")
            elif target not in current_targets:
                problems.append(
                    f"{item_label}.target 必须对应同一份复审报告中的发现：{target}"
                )
        elif verdict == "不成立" and target != "":
            problems.append(f"{item_label}.target 在 verdict 为 不成立 时必须是空字符串")
        if not _nonempty(item["evidence"]):
            problems.append(f"{item_label}.evidence 必须是非空字符串")

    for reference in previous_by_reference:
        count = counts.get(reference, 0)
        if count == 0:
            problems.append(f"{label}.carried 缺少上一轮发现：{reference}")
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
            lines_by_file, allow_single_line=False,
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


def _validate_requirement(requirement, label, features):
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
    feature_id, separator, item_id = source.partition(":")
    if not separator or not feature_id or not item_id or ":" in item_id:
        problems.append(
            f"{label}.source 必须写成 功能 id:条目 id"
        )
        return problems
    feature = features.get(feature_id)
    if feature is None:
        problems.append(f"{label}.source 指向的功能不存在：{feature_id}")
        return problems
    item = next((value for value in feature["items"] if value["id"] == item_id), None)
    if item is None:
        problems.append(f"{label}.source 指向的条目不存在：{source}")
        return problems
    if requirement["text"] != item["text"]:
        problems.append(f"{label}.text 必须与条目原文完全相同：{source}")
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

    def inspect_node(node):
        if isinstance(node, CODE_DEFINITION_TYPES):
            definition_lines.add(node.lineno)
            if isinstance(node, ast.ClassDef):
                inspect_nodes(node.body)
            return
        for child in ast.iter_child_nodes(node):
            inspect_node(child)

    def inspect_nodes(nodes):
        for node in nodes:
            inspect_node(node)

    inspect_nodes(tree.body)
    return definition_lines


def _strip_markdown_container_prefixes(text):
    _quotes, remaining = _strip_markdown_quote_prefixes(text)
    while True:
        list_item = re.match(
            r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)", remaining,
        )
        if list_item is not None:
            remaining = remaining[list_item.end():]
            continue
        return remaining


def _strip_markdown_quote_prefixes(text):
    prefixes = []
    remaining = text
    while True:
        quote = re.match(r"^ {0,3}>[ \t]?", remaining)
        if quote is None:
            return tuple(prefixes), remaining
        prefixes.append(">")
        remaining = remaining[quote.end():]


def _markdown_code_lines(file_lines, excluded_lines):
    code_lines = set()
    fence_character = None
    fence_length = 0
    for index, line in enumerate(file_lines):
        if index in excluded_lines:
            continue
        text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
        container_text = _strip_markdown_container_prefixes(text)
        if fence_character is not None:
            code_lines.add(index)
            closing = re.match(r"^ {0,3}(`+|~+)[ \t]*$", container_text)
            if (closing is not None and closing.group(1)[0] == fence_character
                    and len(closing.group(1)) >= fence_length):
                fence_character = None
                fence_length = 0
            continue
        # Four-space indents are Markdown code; shorter leading whitespace is table indentation.
        if container_text.startswith("    ") or container_text.startswith("\t"):
            code_lines.add(index)
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})", container_text)
        if opening is not None:
            code_lines.add(index)
            fence_character = opening.group(1)[0]
            fence_length = len(opening.group(1))
    return code_lines


def _markdown_heading_lines(file_lines):
    headings = set()
    texts = [
        line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
        for line in file_lines
    ]
    yaml_header_lines = set()
    yaml_header_end = None
    if texts and texts[0].strip() == "---":
        yaml_header_end = next(
            (index for index in range(1, len(texts)) if texts[index].strip() == "---"),
            None,
        )
        if yaml_header_end is not None:
            yaml_header_lines.update(range(yaml_header_end + 1))
    hidden_lines = yaml_header_lines | _markdown_code_lines(texts, yaml_header_lines)
    for index, text in enumerate(texts):
        if index in hidden_lines:
            continue
        container_text = _strip_markdown_container_prefixes(text)
        if MARKDOWN_HEADING.match(container_text):
            headings.add(index + 1)

    for index, text in enumerate(texts):
        if index in hidden_lines or index == 0 or index - 1 in hidden_lines:
            continue
        quote_prefixes, underline = _strip_markdown_quote_prefixes(text)
        paragraph_quotes, paragraph = _strip_markdown_quote_prefixes(texts[index - 1])
        if quote_prefixes != paragraph_quotes:
            continue
        paragraph_list = re.match(
            r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)", paragraph,
        )
        underline_list = re.match(
            r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)", underline,
        )
        if paragraph_list is not None:
            list_indent = paragraph_list.end()
            indentation = len(underline) - len(underline.lstrip(" "))
            if indentation < list_indent:
                continue
            paragraph = paragraph[list_indent:]
            underline = underline[list_indent:]
        elif underline_list is not None:
            continue
        if not SETEXT_UNDERLINE.match(underline):
            continue
        if not paragraph.strip() or MARKDOWN_HEADING.match(paragraph):
            continue
        if paragraph.startswith(("    ", "\t")) or SETEXT_UNDERLINE.match(paragraph):
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


def _validate_keep_items(items, label, repository, feature, features, keep_files,
                         finding_ranges, feature_targets, duplicate_windows,
                         tracked_files):
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
            item.get("requirement"), f"{item_label}.requirement", features,
        ))
        line_spec = item.get("lines")
        if not _nonempty(line_spec):
            continue
        line_range, range_problems = _checked_line_range(
            line_spec, item_label, "lines", repository, keep_files,
            lines_by_file, allow_single_line=False,
        )
        problems.extend(range_problems)
        if line_range is None:
            continue
        duplicate_ranges = other_ranges_for_keep(line_range, duplicate_windows)
        if duplicate_ranges and not _cost_mentions_duplicate_location(
            item["cost"], duplicate_ranges, item_label, repository, tracked_files,
        ):
            locations = ", ".join(
                f"{path}:{start}-{end}"
                for path, start, end in duplicate_ranges
            )
            problems.append(
                f"{item_label}.cost 要写出另一份并说明为什么两份都要留；能并的提「合并」。"
                f"本条碰到的另一份在：{locations}"
            )
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
    for finding, target_range in finding_ranges:
        if finding.get("kind") not in {"删除", "合并", "孤儿"}:
            continue
        # 删除和合并会移除目标；孤儿没有可引用条目，去留由复审试删和用户决定。
        path, start_line, end_line = target_range
        if path in keep_files:
            covered_ranges.setdefault(path, []).append((start_line, end_line))
    problems.extend(_uncovered_nonempty_lines(
        keep_files, covered_ranges, label, "功能文件 ", repository, lines_by_file,
    ))
    return problems


def _cost_mentions_duplicate_location(cost, duplicate_ranges, label,
                                      repository, tracked_files):
    if not isinstance(cost, str):
        return False
    lines_by_file = {}
    tracked_set = set(tracked_files)
    candidate_paths = {path for path, _start, _end in duplicate_ranges}
    for _path, pattern in _tracked_location_patterns(candidate_paths):
        for match in pattern.finditer(cost):
            location, errors = _checked_line_range(
                match.group(0), label, "cost", repository, tracked_set,
                lines_by_file, allow_single_line=True,
            )
            if errors or location is None:
                continue
            path, start_line, end_line = location
            if any(
                path == other_path
                and start_line <= other_end
                and other_start <= end_line
                for other_path, other_start, other_end in duplicate_ranges
            ):
                return True
    return False


def _candidate_artifact_path(value, label, base_directory, problems):
    if not _nonempty(value):
        problems.append(f"{label} 必须是相对路径字符串")
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        problems.append(f"{label} 必须是相对路径且不能越出 JSON 所在目录：{value}")
        return None
    path = (base_directory / relative).resolve()
    try:
        path.relative_to(base_directory.resolve())
    except ValueError:
        problems.append(f"{label} 必须留在 JSON 所在目录内：{value}")
        return None
    if not path.is_file():
        problems.append(f"{label} 必须指向已有文件：{value}")
        return None
    return path


def _candidate_repository_path(value, label, repository, problems, allow_root):
    if not _nonempty(value):
        problems.append(f"{label} 必须是仓内相对路径")
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        problems.append(f"{label} 必须是仓内相对路径：{value}")
        return None
    if value == "." and allow_root:
        path = Path(repository).resolve()
        normalized = "."
    else:
        path = (Path(repository) / relative).resolve()
        normalized = relative.as_posix()
    try:
        path.relative_to(Path(repository).resolve())
    except ValueError:
        problems.append(f"{label} 必须留在仓内：{value}")
        return None
    if not path.exists():
        problems.append(f"{label} 指向的路径不存在：{value}")
        return None
    return normalized


def _candidate_xml_cases(path, label, problems):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        problems.append(f"{label} 不是可读的 JUnit XML：{error}")
        return []
    return [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "testcase"]


def _pytest_case(case, root, repository, label, problems):
    values = {}
    for key in ("classname", "name", "file", "line"):
        value = case.get(key)
        if not _nonempty(value):
            problems.append(f"{label} 缺少 {key}")
        else:
            values[key] = value
    if not all(key in values for key in ("classname", "name", "file", "line")):
        return None
    if not values["line"].isascii() or not values["line"].isdecimal():
        problems.append(f"{label}.line 必须是从 0 开始的非负整数：{values['line']}")
        return None
    file_path = Path(values["file"])
    if file_path.is_absolute() or ".." in file_path.parts:
        problems.append(f"{label}.file 必须相对 root：{values['file']}")
        return None
    full_path = Path(values["file"]) if root == "." else Path(root) / file_path
    full_path = full_path.as_posix()
    if not (Path(repository) / full_path).is_file():
        problems.append(f"{label}.file 拼入 root 后在仓里不存在：{full_path}")
        return None
    values["path"] = full_path
    values["test_id"] = f"{full_path}::{values['classname']}::{values['name']}"
    # pytest JUnit reports zero-based lines; function IDs and source locations are one-based.
    values["function_line"] = int(values["line"]) + 1
    values["function_name"] = values["name"].split("[", 1)[0]
    values["function_id"] = (
        f"{full_path}:{values['function_line']}::{values['function_name']}"
    )
    values["identity"] = tuple(values[key] for key in ("classname", "name", "path", "line"))
    values["element"] = case
    return values


def _nextest_list_items(listing, root, label, problems):
    suites = listing.get("rust-suites") if isinstance(listing, dict) else None
    if not isinstance(suites, dict):
        problems.append(f"{label}.rust-suites 必须是按二进制 id 索引的对象")
        return []
    items = []
    for binary_id, suite in suites.items():
        suite_label = f"{label}.rust-suites[{binary_id}]"
        if not isinstance(binary_id, str) or not _nonempty(binary_id):
            problems.append(f"{suite_label} 二进制 id 必须是非空字符串")
            continue
        if not isinstance(suite, dict):
            problems.append(f"{suite_label} 必须是对象")
            continue
        if suite.get("status") != "listed":
            problems.append(f"{suite_label}.status 必须是 listed")
        testcases = suite.get("testcases")
        if not isinstance(testcases, dict):
            problems.append(f"{suite_label}.testcases 必须是对象")
            continue
        for test_name, testcase in testcases.items():
            testcase_label = f"{suite_label}.testcases[{test_name}]"
            if not isinstance(test_name, str) or not _nonempty(test_name):
                problems.append(f"{testcase_label} 测试路径必须是非空字符串")
                continue
            if not isinstance(testcase, dict):
                problems.append(f"{testcase_label} 必须是对象")
                continue
            filter_match = testcase.get("filter-match")
            status = filter_match.get("status") if isinstance(filter_match, dict) else None
            reason = filter_match.get("reason") if isinstance(filter_match, dict) else None
            if status != "matches" and reason != "ignored":
                problems.append(
                    f"{testcase_label} 不能因筛选排除测试（filter-match={reason or status!r}）"
                )
                continue
            test_id = f"{root}::{binary_id}::{test_name}"
            items.append({
                "test_id": test_id,
                "name": test_name,
                "function_id": test_id,
                "function_name": test_name.rsplit("::", 1)[-1],
                "runner": "nextest",
            })
    return items


def _rust_test_attribute(lines, line_number):
    index = line_number - 2
    while index >= 0 and lines[index].strip().startswith("#["):
        if re.fullmatch(r"#\[(?:[A-Za-z_]\w*::)*test(?:\s*\([^]]*\))?\]", lines[index].strip()):
            return True
        index -= 1
    return False


def _rust_test_range(lines, line_number):
    start = line_number
    index = line_number - 2
    while index >= 0 and lines[index].strip().startswith(("#[", "///")):
        start = index + 1
        index -= 1
    function_indent = len(lines[line_number - 1]) - len(lines[line_number - 1].lstrip())
    for index in range(line_number, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= function_indent:
            if line.strip() == "}":
                return start, index + 1
            return None
    return None


def _python_test_range(path, file_lines, line_number, function_name):
    source = b"\n".join(
        line if isinstance(line, bytes) else str(line).encode("utf-8")
        for line in file_lines
    )
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, UnicodeDecodeError):
        return None
    matching = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function_name:
            continue
        start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
        if start == line_number:
            matching.append((start, node.end_lineno or node.lineno))
    return matching[0] if len(matching) == 1 else None


def _test_functions_for_features(data, repository, expected_features, skip_paths):
    paths = set()
    for feature_id in expected_features:
        feature = data["features"][feature_id]
        direct_files = {
            path for values in feature["files"].values() for path in values
        }
        for path in direct_files:
            if is_skipped_path(path, skip_paths):
                continue
            category = file_category_with_skip(repository, path, skip_paths)
            if category == "tests":
                paths.add(path)
            elif Path(path).suffix == ".rs" and category == "code":
                lines = _read_file_lines(Path(repository) / path)
                if lines and any(
                    re.fullmatch(r"#\[(?:[A-Za-z_]\w*::)*test(?:\s*\([^]]*\))?\]",
                                 line.decode("utf-8", errors="replace").strip()
                                 if isinstance(line, bytes) else str(line).strip())
                    for line in lines
                ):
                    paths.add(path)
    return paths


def _source_line_range(value, label, field, repository, tracked_files,
                       lines_by_file, problems, allow_single):
    line_range, errors = _checked_line_range(
        value, label, field, repository, tracked_files, lines_by_file,
        allow_single_line=allow_single,
    )
    problems.extend(errors)
    if line_range is None:
        return None
    path, _start, _end = line_range
    if file_category_with_skip(repository, path, []) != "code":
        problems.append(f"{label}.{field} 必须覆盖非测试代码文件：{path}")
        return None
    return line_range


def _expand_line_ranges(ranges):
    return {
        (path, line)
        for path, start, end in ranges
        for line in range(start, end + 1)
    }


def _validate_test_candidates(document, document_path, repository, data,
                              expected_features, tracked_files):
    """Validate test collection evidence and return ranges used by razor review."""
    problems = _field_errors(document, TEST_CANDIDATE_KEYS, "test-candidates")
    if not isinstance(document, dict):
        return problems, []

    base_directory = Path(document_path).resolve().parent
    runs = document.get("runs")
    if not isinstance(runs, list):
        problems.append("test-candidates.runs 必须是数组")
        runs = []
    all_items = {}
    function_items = {}
    item_results = {}
    source_lines_by_file = {}
    for run_index, run in enumerate(runs):
        label = f"test-candidates.runs[{run_index}]"
        if not isinstance(run, dict):
            problems.append(f"{label} 必须是对象")
            continue
        runner = run.get("runner")
        if not isinstance(runner, str) or runner not in TEST_RUN_KEYS:
            problems.append(f"{label}.runner 只能是 pytest 或 nextest")
            continue
        problems.extend(_field_errors(run, TEST_RUN_KEYS[runner], label))
        root = _candidate_repository_path(
            run.get("root"), f"{label}.root", repository, problems, allow_root=True,
        )
        if root is None:
            continue
        root_directory = Path(repository) if root == "." else Path(repository) / root
        if not root_directory.is_dir():
            problems.append(f"{label}.root 必须指向仓内目录：{root}")
            continue
        if not _nonempty(run.get("command")):
            problems.append(f"{label}.command 必须是非空字符串")
        list_path = _candidate_artifact_path(
            run.get("list"), f"{label}.list", base_directory, problems,
        )
        junit_path = _candidate_artifact_path(
            run.get("junit"), f"{label}.junit", base_directory, problems,
        )
        if runner == "pytest":
            log_path = _candidate_artifact_path(
                run.get("list_log"), f"{label}.list_log", base_directory, problems,
            )
            if log_path is not None:
                try:
                    log = log_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as error:
                    problems.append(f"{label}.list_log 无法读取：{error}")
                else:
                    last_line = next((line for line in reversed(log.splitlines()) if line.strip()), "")
                    summary = last_line.strip("= \t\r\n").strip()
                    lowered = summary.lower()
                    if (not lowered.startswith("no tests ran")
                            or "deselected" in lowered or "error" in lowered):
                        problems.append(
                            f"{label}.list_log 最后一个非空行必须表示 no tests ran，且没有 deselected/error："
                            f"{summary}"
                        )
            list_cases = (
                _candidate_xml_cases(list_path, f"{label}.list", problems)
                if list_path is not None else []
            )
            run_cases = (
                _candidate_xml_cases(junit_path, f"{label}.junit", problems)
                if junit_path is not None else []
            )
            listed_by_identity = {}
            run_by_identity = {}
            for case_index, testcase in enumerate(list_cases):
                item = _pytest_case(
                    testcase, root, repository,
                    f"{label}.list testcase[{case_index}]", problems,
                )
                if item is None:
                    continue
                if item["test_id"] in all_items or item["test_id"] in listed_by_identity:
                    problems.append(f"{label}.list 测试项重复：{item['test_id']}")
                    continue
                listed_by_identity[item["test_id"]] = item
                all_items[item["test_id"]] = item
                function = function_items.setdefault(item["function_id"], {
                    "id": item["function_id"], "runner": runner,
                    "file": item["path"], "line": item["function_line"],
                    "name": item["function_name"], "items": set(),
                })
                if function["file"] != item["path"] or function["line"] != item["function_line"]:
                    problems.append(f"测试函数身份冲突：{item['function_id']}")
                function["items"].add(item["test_id"])
            for case_index, testcase in enumerate(run_cases):
                item = _pytest_case(
                    testcase, root, repository,
                    f"{label}.junit testcase[{case_index}]", problems,
                )
                if item is None:
                    continue
                if item["test_id"] in run_by_identity:
                    problems.append(f"{label}.junit 测试项重复：{item['test_id']}")
                    continue
                run_by_identity[item["test_id"]] = item
                listed = listed_by_identity.get(item["test_id"])
                if listed is None or listed["identity"] != item["identity"]:
                    problems.append(f"{label}.junit 测试项在完整全集里没有完全相同的一项：{item['test_id']}")
                    continue
                item_results[item["test_id"]] = testcase
        else:
            if list_path is not None:
                try:
                    listing = json.loads(
                        list_path.read_text(encoding="utf-8"),
                        object_pairs_hook=object_without_duplicate_keys,
                    )
                except (OSError, UnicodeError, ValueError) as error:
                    problems.append(f"{label}.list 无法读取 nextest JSON：{error}")
                    listing = None
            else:
                listing = None
            listed_items = _nextest_list_items(listing, root, f"{label}.list", problems)
            listed_by_identity = {}
            for item in listed_items:
                if item["test_id"] in all_items or item["test_id"] in listed_by_identity:
                    problems.append(f"{label}.list 测试项重复：{item['test_id']}")
                    continue
                listed_by_identity[item["test_id"]] = item
                all_items[item["test_id"]] = item
                function_items[item["function_id"]] = {
                    "id": item["function_id"], "runner": runner,
                    "file": None, "line": None, "name": item["function_name"],
                    "items": {item["test_id"]},
                }
            if junit_path is not None:
                try:
                    junit_root = ET.parse(junit_path).getroot()
                except (OSError, ET.ParseError):
                    junit_root = None
                run_by_identity = {}
                if junit_root is not None:
                    for suite in junit_root.iter():
                        if suite.tag.rsplit("}", 1)[-1] != "testsuite":
                            continue
                        binary_id = suite.get("name")
                        for testcase in suite:
                            if testcase.tag.rsplit("}", 1)[-1] != "testcase":
                                continue
                            test_name = testcase.get("name")
                            if not _nonempty(binary_id) or not _nonempty(test_name):
                                problems.append(f"{label}.junit testcase 必须有 testsuite.name 和 testcase.name")
                                continue
                            test_id = f"{root}::{binary_id}::{test_name}"
                            if test_id in run_by_identity:
                                problems.append(f"{label}.junit 测试项重复：{test_id}")
                                continue
                            run_by_identity[test_id] = testcase
                            if test_id not in listed_by_identity:
                                problems.append(f"{label}.junit 测试项不在完整全集里：{test_id}")
                                continue
                            item_results[test_id] = testcase

    # Any missing or failed parameter/inheritance instance makes its source function unmeasured.
    must_be_unmeasured = set()
    for function_id, function in function_items.items():
        for test_id in function["items"]:
            result = item_results.get(test_id)
            if result is None or any(
                child.tag.rsplit("}", 1)[-1] not in {"system-out", "system-err", "properties"}
                for child in result
            ):
                must_be_unmeasured.add(function_id)

    unmeasured_files = document.get("unmeasured_files")
    if not isinstance(unmeasured_files, list):
        problems.append("test-candidates.unmeasured_files 必须是数组")
        unmeasured_files = []
    unmeasured_file_paths = set()
    for index, item in enumerate(unmeasured_files):
        label = f"test-candidates.unmeasured_files[{index}]"
        item_problems = _field_errors(item, {"path", "why"}, label)
        problems.extend(item_problems)
        if item_problems or not isinstance(item, dict):
            continue
        path = _candidate_repository_path(
            item.get("path"), f"{label}.path", repository, problems, allow_root=False,
        )
        if path is not None:
            if path in unmeasured_file_paths:
                problems.append(f"unmeasured_files 文件重复：{path}")
            unmeasured_file_paths.add(path)
        if not _nonempty(item.get("why")):
            problems.append(f"{label}.why 必须是非空字符串")

    generated_mutants = document.get("generated_mutants")
    if not isinstance(generated_mutants, int) or isinstance(generated_mutants, bool) or generated_mutants < 0:
        problems.append("test-candidates.generated_mutants 必须是非负整数")
        generated_mutants = 0
    mutants = document.get("mutants")
    if not isinstance(mutants, list):
        problems.append("test-candidates.mutants 必须是数组")
        mutants = []
    mutant_ids = set()
    mutant_locations = {}
    for index, mutant in enumerate(mutants):
        label = f"test-candidates.mutants[{index}]"
        item_problems = _field_errors(mutant, {"id", "location", "change"}, label)
        problems.extend(item_problems)
        if item_problems or not isinstance(mutant, dict):
            continue
        mutant_id = mutant.get("id")
        mutant_id_is_valid = _nonempty(mutant_id)
        if not mutant_id_is_valid:
            problems.append(f"{label}.id 必须是非空字符串")
        elif mutant_id in mutant_ids:
            problems.append(f"变异 id 重复：{mutant_id}")
        else:
            mutant_ids.add(mutant_id)
        if not _nonempty(mutant.get("change")):
            problems.append(f"{label}.change 必须是非空字符串")
        location = _source_line_range(
            mutant.get("location"), label, "location", repository,
            tracked_files, source_lines_by_file, problems, allow_single=True,
        )
        if location is not None and mutant_id_is_valid:
            mutant_locations[mutant_id] = location
    if len(mutants) > generated_mutants:
        problems.append("test-candidates.mutants 数量不能多于 generated_mutants")

    functions = document.get("functions")
    if not isinstance(functions, dict):
        problems.append("test-candidates.functions 必须是对象")
        functions = {}
    unmeasured_functions = document.get("unmeasured_functions")
    if not isinstance(unmeasured_functions, list):
        problems.append("test-candidates.unmeasured_functions 必须是数组")
        unmeasured_functions = []
    unmeasured_by_id = {}
    for index, item in enumerate(unmeasured_functions):
        label = f"test-candidates.unmeasured_functions[{index}]"
        item_problems = _field_errors(item, {"id", "file", "why"}, label)
        problems.extend(item_problems)
        if item_problems or not isinstance(item, dict):
            continue
        function_id = item.get("id")
        if not _nonempty(function_id):
            problems.append(f"{label}.id 必须是非空字符串")
        elif function_id in unmeasured_by_id:
            problems.append(f"量不了函数重复：{function_id}")
        else:
            unmeasured_by_id[function_id] = item
        if not _nonempty(item.get("why")):
            problems.append(f"{label}.why 必须是非空字符串")

    expected_ids = set(function_items)
    measured_ids = set(functions)
    unmeasured_ids = set(unmeasured_by_id)
    if measured_ids & unmeasured_ids:
        problems.append("functions 和 unmeasured_functions 的函数不能重叠")
    if measured_ids | unmeasured_ids != expected_ids:
        missing = sorted(expected_ids - measured_ids - unmeasured_ids)
        extra = sorted((measured_ids | unmeasured_ids) - expected_ids)
        if missing:
            problems.append(f"test-candidates 函数清单缺少函数：{', '.join(missing)}")
        if extra:
            problems.append(f"test-candidates 函数清单多出函数：{', '.join(extra)}")
    for function_id in sorted(must_be_unmeasured & measured_ids):
        problems.append(
            f"测试项未运行或没通过，函数必须量不了并记在 unmeasured_functions：{function_id}"
        )

    function_ranges = {}
    measured_covers = {}
    measured_kills = {}
    duplicate_nextest_source = {}
    for function_id, function_data in functions.items():
        label = f"test-candidates.functions[{function_id}]"
        field_problems = _field_errors(
            function_data, {"file", "line", "covers", "kills"}, label,
        )
        problems.extend(field_problems)
        if field_problems or not isinstance(function_data, dict):
            continue
        derived = function_items.get(function_id)
        file_path = _candidate_repository_path(
            function_data.get("file"), f"{label}.file", repository, problems,
            allow_root=False,
        )
        line_number = function_data.get("line")
        if not isinstance(line_number, int) or isinstance(line_number, bool) or line_number < 1:
            problems.append(f"{label}.line 必须是正整数")
            line_number = None
        if derived is not None and derived["runner"] == "pytest":
            if file_path != derived["file"]:
                problems.append(f"{label}.file 必须等于从 pytest 全集推出的位置：{derived['file']}")
            if line_number != derived["line"]:
                problems.append(f"{label}.line 必须等于 pytest JUnit line 加 1：{derived['line']}")
        elif derived is not None and derived["runner"] == "nextest":
            if file_path is not None:
                derived["file"] = file_path
            if file_path is not None and line_number is not None:
                source_lines = _read_file_lines(Path(repository) / file_path)
                if source_lines is not None:
                    decoded = [line.decode("utf-8", errors="replace") if isinstance(line, bytes)
                               else str(line) for line in source_lines]
                    name = derived["name"]
                    source_line = decoded[line_number - 1] if line_number <= len(decoded) else ""
                    if line_number > len(decoded) or not re.search(
                        rf"\bfn\s+{re.escape(name)}(?=[(<])", source_line,
                    ):
                        problems.append(f"{label}.line 必须是 fn {name}( 或 fn {name}< 所在行")
                    elif not _rust_test_attribute(decoded, line_number):
                        problems.append(f"{label}.line 上方必须紧挨 Rust 测试属性 #[test] 或 #[路径::test]")
                    else:
                        source_key = (file_path, line_number)
                        if source_key in duplicate_nextest_source:
                            problems.append(
                                f"两个已测 nextest 函数不能使用相同 file、line："
                                f"{file_path}:{line_number}"
                            )
                        duplicate_nextest_source[source_key] = function_id
                        function_ranges[function_id] = _rust_test_range(decoded, line_number)
        covers = function_data.get("covers")
        if not isinstance(covers, list) or not covers:
            problems.append(f"{label}.covers 必须是非空数组")
            covers = []
        cover_ranges = []
        for index, item in enumerate(covers):
            parsed = _source_line_range(
                item, label, f"covers[{index}]", repository,
                tracked_files, source_lines_by_file, problems, allow_single=False,
            )
            if parsed is not None:
                cover_ranges.append(parsed)
        cover_lines = _expand_line_ranges(cover_ranges)
        if len(cover_lines) != sum(end - start + 1 for _path, start, end in cover_ranges):
            problems.append(f"{label}.covers 不能重复包含同一代码行")
        measured_covers[function_id] = cover_lines
        kills = function_data.get("kills")
        if not isinstance(kills, list):
            problems.append(f"{label}.kills 必须是数组")
            kills = []
        if len(set(kill for kill in kills if isinstance(kill, str))) != len(kills):
            problems.append(f"{label}.kills 不能重复变异 id")
        for mutant_id in kills:
            if not isinstance(mutant_id, str) or mutant_id not in mutant_ids:
                problems.append(f"{label}.kills 引用了 mutants 中不存在的 id：{mutant_id}")
        measured_kills[function_id] = set(kill for kill in kills if isinstance(kill, str))
        if derived is not None and derived["runner"] == "pytest" and file_path is not None:
            lines = _read_file_lines(Path(repository) / file_path)
            if lines is not None:
                function_ranges[function_id] = _python_test_range(
                    file_path, lines, derived["line"], derived["name"],
                )

    for function_id, item in unmeasured_by_id.items():
        derived = function_items.get(function_id)
        path = _candidate_repository_path(
            item.get("file"), f"unmeasured_functions[{function_id}].file",
            repository, problems, allow_root=False,
        )
        if derived is not None and derived["runner"] == "pytest" and path != derived["file"]:
            problems.append(
                f"unmeasured_functions[{function_id}].file 必须等于 pytest 全集位置：{derived['file']}"
            )
        if derived is not None and derived["runner"] == "nextest" and path is not None:
            derived["file"] = path

    if functions and not mutants:
        problems.append("有已测函数时 mutants 不能为空")

    skip_paths = read_skip_paths(repository, tracked_files)
    test_files = _test_functions_for_features(data, repository, expected_features, skip_paths)
    all_direct_functions = {}
    for function_id, derived in function_items.items():
        measured_function = functions.get(function_id)
        measured_file = (
            measured_function.get("file") if isinstance(measured_function, dict) else None
        )
        function_file = measured_file or derived["file"]
        if function_file is None and function_id in unmeasured_by_id:
            function_file = unmeasured_by_id[function_id].get("file")
        if isinstance(function_file, str):
            all_direct_functions.setdefault(function_file, []).append(function_id)
            if function_file not in test_files:
                problems.append(
                    f"函数 {function_id} 的 file 必须是本次范围内的直属测试文件：{function_file}"
                )
    for path in sorted(unmeasured_file_paths):
        if path not in test_files:
            problems.append(f"unmeasured_files.path 不是范围内的直属测试文件：{path}")
        if all_direct_functions.get(path):
            problems.append(f"测试文件不能同时有函数记录和 unmeasured_files：{path}")
    for path in sorted(test_files):
        has_functions = bool(all_direct_functions.get(path))
        has_unmeasured = path in unmeasured_file_paths
        if has_functions == has_unmeasured:
            problems.append(
                f"直属测试文件 {path} 必须恰好有函数记录或 unmeasured_files 原因"
            )

    for function_id, cover_lines in measured_covers.items():
        for other_id, source_range in function_ranges.items():
            if source_range is None:
                continue
            other_file = function_items.get(other_id, {}).get("file")
            if other_file is not None and any(
                path == other_file and source_range[0] <= line <= source_range[1]
                for path, line in cover_lines
            ):
                problems.append(
                    f"functions[{function_id}].covers 不能包含测试函数 {other_id} 自身的行"
                )
    test_source_lines = {
        (function_items[function_id]["file"], line)
        for function_id, source_range in function_ranges.items()
        if source_range is not None and function_items.get(function_id, {}).get("file") is not None
        for line in range(source_range[0], source_range[1] + 1)
    }
    for mutant_id, location in mutant_locations.items():
        if location is not None and any(
            (location[0], line) in test_source_lines
            for line in range(location[1], location[2] + 1)
        ):
            problems.append(f"mutants[{mutant_id}].location 不能指向测试函数自身的行")

    candidate_rows = document.get("candidates")
    if not isinstance(candidate_rows, list):
        problems.append("test-candidates.candidates 必须是数组")
        candidate_rows = []
    ordered_functions = sorted(
        measured_ids,
        key=lambda function_id: (-len(measured_covers.get(function_id, set())), function_id),
    )
    computed_candidates = []
    selected_candidate_ids = set()
    for function_id in ordered_functions:
        # Once selected, a candidate cannot witness a later candidate: all surviving tests
        # must still contain the evidence after every candidate is removed.
        witnesses = [
            other_id for other_id in ordered_functions
            if other_id != function_id and other_id not in selected_candidate_ids
        ]
        covered_lines = set().union(*(measured_covers.get(item, set()) for item in witnesses))
        killed_mutants = set().union(*(measured_kills.get(item, set()) for item in witnesses))
        if (measured_covers.get(function_id, set()) <= covered_lines
                and measured_kills.get(function_id, set()) <= killed_mutants):
            selected_candidate_ids.add(function_id)
            computed_candidates.append(function_id)

    candidate_info_by_id = {}
    seen_candidates = set()
    supplied_candidate_ids = {
        row.get("function") for row in candidate_rows
        if isinstance(row, dict) and isinstance(row.get("function"), str)
    }
    if supplied_candidate_ids != set(computed_candidates):
        problems.append("candidates 函数集合必须与检查器重算结果完全一致")
    for index, row in enumerate(candidate_rows):
        label = f"test-candidates.candidates[{index}]"
        field_problems = _field_errors(row, {"function", "covered_by"}, label)
        problems.extend(field_problems)
        if field_problems or not isinstance(row, dict):
            continue
        function_id = row.get("function")
        if not _nonempty(function_id):
            problems.append(f"{label}.function 必须是非空字符串")
            continue
        if function_id in seen_candidates:
            problems.append(f"候选函数重复：{function_id}")
        seen_candidates.add(function_id)
        if not isinstance(function_id, str) or function_id not in functions:
            problems.append(f"候选必须引用已测函数：{function_id}")
        if not isinstance(function_id, str) or function_id not in selected_candidate_ids:
            continue
        derived = function_items.get(function_id)
        function_data = functions.get(function_id)
        covered_by = row.get("covered_by")
        if not isinstance(covered_by, list) or not covered_by:
            problems.append(f"{label}.covered_by 必须是非空数组")
            covered_by = []
        if len(set(value for value in covered_by if isinstance(value, str))) != len(covered_by):
            problems.append(f"{label}.covered_by 不能重复函数 id")
        witness_ids = []
        for witness_id in covered_by:
            if not isinstance(witness_id, str):
                problems.append(f"{label}.covered_by 的函数 id 必须是字符串")
                continue
            if witness_id in selected_candidate_ids:
                problems.append(f"{label}.covered_by 不能包含候选函数：{witness_id}")
            if witness_id not in functions:
                problems.append(f"{label}.covered_by 必须引用已测函数：{witness_id}")
            else:
                witness_ids.append(witness_id)
        witness_lines = set().union(*(measured_covers.get(item, set()) for item in witness_ids))
        witness_kills = set().union(*(measured_kills.get(item, set()) for item in witness_ids))
        if not measured_covers.get(function_id, set()) <= witness_lines:
            problems.append(f"{label}.covered_by 合起来没有包含候选 covers")
        if not measured_kills.get(function_id, set()) <= witness_kills:
            problems.append(f"{label}.covered_by 合起来没有包含候选 kills")
        if derived is not None and function_data is not None:
            file_path = function_data.get("file")
            line_number = function_data.get("line")
            full_range = function_ranges.get(function_id)
            if file_path is not None and line_number is not None and full_range is None:
                problems.append(
                    f"候选 {function_id} 的完整函数范围无法识别；将它改记 unmeasured_functions 后重算候选"
                )
            elif full_range is not None:
                candidate_info_by_id[function_id] = {
                    "function": function_id, "file": file_path,
                    "start": full_range[0], "end": full_range[1],
                    "covered_by": witness_ids,
                    "witness_locations": [
                        (functions[witness_id]["file"], functions[witness_id]["line"])
                        for witness_id in witness_ids
                        if isinstance(functions.get(witness_id), dict)
                        and isinstance(functions[witness_id].get("file"), str)
                        and isinstance(functions[witness_id].get("line"), int)
                        and not isinstance(functions[witness_id].get("line"), bool)
                    ],
                }

    return problems, list(candidate_info_by_id.values())


def _validate_razor_test_candidates(candidates, review, feature_id, direct_files,
                                    keep_files, finding_ranges, feature_targets,
                                    repository, tracked_files, label):
    problems = []
    if not candidates:
        return problems
    keep_items = review.get("keep") if isinstance(review.get("keep"), list) else []
    keep_ranges = []
    for item in keep_items:
        if not isinstance(item, dict):
            continue
        parsed = _parse_line_range(item.get("lines"), allow_single_line=False)
        if parsed is not None:
            keep_ranges.append((parsed, item))
    range_reductions = [
        (finding, location) for finding, location in finding_ranges
        if finding.get("kind") in {"删除", "合并"}
    ]
    for candidate in candidates:
        path = candidate["file"]
        start, end = candidate["start"], candidate["end"]
        if path not in direct_files or path not in keep_files:
            continue
        candidate_label = f"候选 {candidate['function']} 的完整范围 {path}:{start}-{end}"
        witness_ranges = [
            (path, line, line) for path, line in candidate.get("witness_locations", [])
        ]
        witness_positions = []
        for witness_file, witness_line in candidate.get("witness_locations", []):
            witness_positions.append(f"{witness_file}:{witness_line}")
        # A whole-function deletion or merge is an explicit answer, including a feature-level one.
        if any(finding.get("kind") in {"删除", "合并"} for finding in feature_targets):
            continue
        fully_reduced = any(
            location[0] == path and location[1] <= start and location[2] >= end
            for _finding, location in range_reductions
        )
        if fully_reduced:
            continue
        overlapping_reduction = any(
            location[0] == path and location[1] <= end and start <= location[2]
            for _finding, location in range_reductions
        )
        overlapping_keep = [
            (line_range, item) for line_range, item in keep_ranges
            if line_range[0] == path and line_range[1] <= end and start <= line_range[2]
        ]
        file_lines = _read_file_lines(Path(repository) / path) or []
        decoded = [line.decode("utf-8", errors="replace") if isinstance(line, bytes)
                   else str(line) for line in file_lines]
        kept_whole = all(
            not decoded[line_number - 1].strip()
            or any(
                line_range[0] == path and line_range[1] <= line_number <= line_range[2]
                for line_range, _item in keep_ranges
            )
            for line_number in range(start, end + 1)
        )
        # A refactor or any other finding cannot answer whether this test function itself stays.
        if not overlapping_reduction and kept_whole and overlapping_keep:
            costs_ok = True
            for _line_range, item in overlapping_keep:
                if not _cost_mentions_duplicate_location(
                    item.get("cost"), witness_ranges, candidate_label,
                    repository, tracked_files,
                ):
                    costs_ok = False
                    break
            if costs_ok:
                continue
        action = "删除或合并整段，或完整保留并说明见证位置"
        witnesses = ", ".join(witness_positions) or "无法识别的见证定义行"
        if overlapping_reduction:
            problems.append(f"{candidate_label} 不能只由部分删除/合并或重构发现覆盖；见证：{witnesses}")
        elif not overlapping_keep or not kept_whole:
            problems.append(f"{candidate_label} 必须{action}；见证：{witnesses}")
        elif not all(
            _cost_mentions_duplicate_location(
                item.get("cost"), witness_ranges, candidate_label,
                repository, tracked_files,
            ) for _line_range, item in overlapping_keep
        ):
            problems.append(f"{candidate_label} 的每条 keep.cost 都必须写出有效见证位置：{witnesses}")
        else:
            problems.append(f"{candidate_label} 的响应不完整；见证：{witnesses}")
    return problems


def validate_reports(data, repository, expected_features, report_paths, tracked_files,
                     usage_coverage, carry_over_findings, test_candidate_ranges):
    """Return all structural, coverage, decision, and rule-quote problems."""
    problems = []
    problems.extend(_validate_empty_feature_items(data, expected_features))
    counts = {role: {} for role in ROLES}
    recheck_counts = {}
    required_rechecks = set(carry_over_findings)
    recheck_report_seen = False
    razor_keep_lines = {}
    orphan_targets_by_feature = {}
    razor_keep_reasons = {}
    duplicate_windows = None
    pending_recheck_verdicts = []
    evidence_lines_by_file = {}
    usage_files = (
        usage_coverage.get("files", {})
        if isinstance(usage_coverage, dict)
        and isinstance(usage_coverage.get("files"), dict)
        else {}
    )
    usage_location_patterns = _tracked_location_patterns(tracked_files)
    skip_paths = read_skip_paths(repository, tracked_files)
    tracked_todo_files = {
        path for path in tracked_files
        if Path(path).name == TODO_FILENAME and not is_skipped_path(path, skip_paths)
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
            feature_id = review.get("feature")
            role_keys = FEATURE_KEYS.get(role) if isinstance(role, str) else None
            if role_keys is not None:
                if (role == "剃刀复审" and isinstance(feature_id, str)
                        and feature_id in carry_over_findings):
                    role_keys = role_keys | {"carried"}
                problems.extend(_field_errors(review, role_keys, feature_label))
            if not isinstance(feature_id, str) or feature_id not in data["features"]:
                problems.append(f"{feature_label}.feature 不在功能清单中：{feature_id!r}")
            elif isinstance(role, str) and role in ROLES and feature_id not in expected_features:
                problems.append(f"报告功能不在本次审查名单里：{feature_id}")
            elif isinstance(role, str) and role in ROLES:
                counts[role][feature_id] = counts[role].get(feature_id, 0) + 1
            elif role == "剃刀复审":
                recheck_counts[feature_id] = recheck_counts.get(feature_id, 0) + 1
            if role == "剃刀" and isinstance(feature_id, str):
                problems.extend(_record_razor_keep_reasons(
                    review.get("keep"), label, feature_id, razor_keep_reasons,
                ))
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
                document_files = (
                    {
                        path for path in direct_files
                        if Path(path).name != TODO_FILENAME
                        and file_category_with_skip(repository, path, skip_paths) == "docs"
                    }
                    if role == "剃刀" else set()
                )
                direct_todo_files = {
                    path for path in direct_files
                    if Path(path).name == TODO_FILENAME
                    and not is_skipped_path(path, skip_paths)
                }
                keep_files = (
                    {
                        path for path in direct_files
                        if Path(path).name != TODO_FILENAME
                        and file_category_with_skip(repository, path, skip_paths)
                        in {"code", "tests", "docs"}
                    }
                    if role in ("剃刀", "剃刀复审") else set()
                )
            else:
                direct_files = set()
                document_files = set()
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
            if role == "剃刀" and feature_data is not None:
                rewrite_problems = _validate_rewrites(
                    review.get("rewrites"),
                    f"{feature_label}.rewrites",
                    repository,
                    document_files,
                    findings,
                )
                problems.extend(rewrite_problems)
            finding_ranges = []
            feature_targets = []
            for finding_index, finding in enumerate(findings):
                finding_label = f"{feature_label}.findings[{finding_index}]"
                if (isinstance(role, str) and role in ROLES
                        and isinstance(feature_id, str)
                        and feature_id in expected_features
                        and isinstance(finding, dict)
                        and finding.get("kind") == "孤儿"):
                    required_rechecks.add(feature_id)
                problems.extend(_validate_finding(
                    finding, finding_label, tracked_todo_files,
                    feature_id=feature_id,
                    direct_todo_files=direct_todo_files,
                ))
                if not isinstance(finding, dict) or not _nonempty(finding.get("target")):
                    continue
                target = finding["target"]
                target_range = _parse_line_range(target, allow_single_line=True)
                if target_range is not None and is_skipped_path(target_range[0], skip_paths):
                    problems.append(
                        f"{finding_label}.target 不能落在跳过名单命中的文件上：{target}"
                    )
                    continue
                if not _valid_target(target, data, repository, tracked_files):
                    problems.append(
                        f"{finding_label}.target 必须是功能 id、文件:行号或 文件:起始行-结束行："
                        f"{target}"
                    )
                    continue
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
                if (isinstance(role, str) and role in ROLES
                        and isinstance(feature_id, str)
                        and feature_id in expected_features
                        and finding.get("kind") == "孤儿"):
                    if target_range is None:
                        problems.append(
                            f"{finding_label}.target 孤儿目标必须是文件:行号或 文件:起始行-结束行"
                        )
                    else:
                        path, start_line, end_line = target_range
                        orphan_targets_by_feature.setdefault(feature_id, []).append(
                            f"{path}:{start_line}-{end_line}"
                        )

            if role == "剃刀" and feature_data is not None:
                direct_code_files = {
                    path for path in direct_files
                    if file_category_with_skip(repository, path, skip_paths) == "code"
                }
                problems.extend(_validate_usage_candidates(
                    review.get("usage_candidates"),
                    feature_label,
                    feature_id,
                    direct_code_files,
                    usage_files,
                    finding_ranges,
                    feature_targets,
                    repository,
                    tracked_files,
                    evidence_lines_by_file,
                    usage_location_patterns,
                ))

            # 复审发现目标只能是本功能 id 或 keep 覆盖文件，写不出 TODO 行发现。
            if role in ("主审", "剃刀") and feature_data is not None:
                problems.extend(_validate_feature_todo_reduction(
                    feature_id,
                    feature_targets,
                    finding_ranges,
                    direct_todo_files,
                    feature_label,
                    repository,
                ))

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
                if feature_id in carry_over_findings and "carried" in review:
                    problems.extend(_validate_carried(
                        review["carried"],
                        feature_label,
                        carry_over_findings[feature_id],
                        findings,
                    ))

            if role == "剃刀" and feature_data is not None:
                keep_items = review.get("keep")
                has_keep_items = isinstance(keep_items, list) and bool(keep_items)
                if has_keep_items and duplicate_windows is None:
                    duplicate_windows = find_duplicate_windows(
                        repository, tracked_files, skip_paths,
                    )
                problems.extend(_validate_keep_items(
                    keep_items,
                    f"报告 {label} 的功能 {feature_id}.keep",
                    repository,
                    feature_data,
                    data["features"],
                    keep_files,
                    finding_ranges,
                    feature_targets,
                    duplicate_windows if duplicate_windows is not None else {},
                    tracked_files,
                ))
                problems.extend(_validate_razor_test_candidates(
                    test_candidate_ranges,
                    review,
                    feature_id,
                    direct_files,
                    keep_files,
                    finding_ranges,
                    feature_targets,
                    repository,
                    tracked_files,
                    f"报告 {label} 的功能 {feature_id}",
                ))
                if (isinstance(feature_id, str)
                        and feature_id in expected_features
                        and has_keep_items):
                    # 每个保留了实体的功能都要有独立复审挑战，不按删减多少设门槛。
                    required_rechecks.add(feature_id)

    # Requiring verdicts for keeps and orphan targets prevents skipped review work.
    location_patterns = (
        _tracked_location_patterns(tracked_files) if pending_recheck_verdicts else []
    )
    for (feature_id, verdicts, label, finding_ranges,
         feature_targets) in pending_recheck_verdicts:
        problems.extend(_validate_recheck_verdicts(
            verdicts,
            label,
            razor_keep_lines.get(feature_id, []),
            orphan_targets_by_feature.get(feature_id, []),
            finding_ranges,
            feature_targets,
            repository,
            tracked_files,
            evidence_lines_by_file,
            location_patterns,
            usage_coverage,
            skip_paths,
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


def _read_required_json(path_value, option_name):
    path = Path(path_value)
    if not path.is_file():
        print(
            f"{option_name} 必须指向已有文件：{path}",
            file=sys.stderr,
        )
        return None, path, 2
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_without_duplicate_keys,
        )
    except (OSError, UnicodeError, ValueError) as error:
        print(f"无法读取 {option_name.lstrip('-')} {path}：{error}")
        return None, path, 1
    return document, path, 0


def main(argv):
    try:
        options = parser().parse_args(argv[1:])
    except SystemExit as error:
        return int(error.code)

    if "无" in options.carry_over and options.carry_over != ["无"]:
        print("--carry-over 使用 无 时不能再列报告路径", file=sys.stderr)
        return 2
    carry_over_paths = [] if options.carry_over == ["无"] else options.carry_over

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
        validate_scope(data, scope, "--scope")
    except ArgumentError as error:
        print(str(error), file=sys.stderr)
        return 2

    usage_coverage, _usage_coverage_path, error_code = _read_required_json(
        options.usage_coverage, "--usage-coverage",
    )
    if error_code:
        return error_code
    test_candidates, test_candidates_path, error_code = _read_required_json(
        options.test_candidates, "--test-candidates",
    )
    if error_code:
        return error_code

    report_paths = [Path(path) for path in options.reports]
    missing = [path for path in report_paths if not path.is_file()]
    if missing:
        print("--reports 必须全部指向已有文件：" + ", ".join(map(str, missing)), file=sys.stderr)
        return 2
    missing_carry_over = [Path(path) for path in carry_over_paths if not Path(path).is_file()]
    if missing_carry_over:
        print(
            "--carry-over 必须全部指向已有文件："
            + ", ".join(map(str, missing_carry_over)),
            file=sys.stderr,
        )
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
        scope_features = [
            feature_id
            for feature_id, _reason in review_features(
                data, fingerprints, records, scope, "全部",
            )
        ]
    except (OSError, UnicodeError, ValueError) as error:
        print(str(error))
        return 1

    tracked_files = set(tracked_paths(repository))
    carry_over_findings, carry_over_problems = _read_carry_over_findings(
        carry_over_paths, scope_features,
    )
    problems = _validate_usage_coverage(
        usage_coverage, data, repository, expected, tracked_files,
    )
    candidate_problems, test_candidate_ranges = _validate_test_candidates(
        test_candidates,
        test_candidates_path,
        repository,
        data,
        expected,
        tracked_files,
    )
    problems.extend(candidate_problems)
    problems.extend(validate_reports(
        data, repository, expected, report_paths, tracked_files, usage_coverage,
        carry_over_findings,
        test_candidate_ranges,
    ))
    problems.extend(carry_over_problems)
    if problems:
        print("\n".join(problems))
        return 1
    print("清扫报告检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
