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
        "feature", "necessity", "smallest", "rewrites", "keep", "todo_items", "findings",
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


def _validate_rewrites(rewrites, label, repository, document_files, findings):
    problems = []
    if not isinstance(rewrites, list):
        return [f"{label} 必须是数组"]

    lines_by_file = {}
    ranges_by_file = {}
    texts_by_file = {}
    labels_by_file = {}
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


def _strip_markdown_quote_prefixes(text):
    prefixes = []
    remaining = text
    while True:
        quote = re.match(r"^ {0,3}>[ \t]?", remaining)
        if quote is None:
            return tuple(prefixes), remaining
        prefixes.append(">")
        remaining = remaining[quote.end():]


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
    for _finding, target_range in finding_ranges:
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


def validate_reports(data, repository, expected_features, report_paths, tracked_files):
    """Return all structural, coverage, decision, and rule-quote problems."""
    problems = []
    counts = {role: {} for role in ROLES}
    recheck_counts = {}
    required_rechecks = set()
    recheck_report_seen = False
    razor_keep_lines = {}
    razor_keep_reasons = {}
    duplicate_windows = None
    pending_recheck_verdicts = []
    evidence_lines_by_file = {}
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
                if (isinstance(feature_id, str)
                        and feature_id in expected_features
                        and has_keep_items):
                    # 每个保留了实体的功能都要有独立复审挑战，不按删减多少设门槛。
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
