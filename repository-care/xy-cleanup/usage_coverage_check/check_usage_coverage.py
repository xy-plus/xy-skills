import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys


XY_CLEANUP_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(XY_CLEANUP_DIRECTORY))
from cleanup_imports import setup_cleanup_imports  # noqa: E402

setup_cleanup_imports(__file__, ())
from approvals import plan_data  # noqa: E402
from cli_errors import report_cli_argument_error, report_cli_input_error  # noqa: E402
from json_validation import object_without_duplicate_keys  # noqa: E402
from repo_map_lib.git_repository import ArgumentError, tracked_paths  # noqa: E402
from repo_map_lib.map_data import file_category_with_skip  # noqa: E402
from repo_map_lib.skip_paths.skip_paths import read_skip_paths  # noqa: E402


MARKDOWN_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")
SETEXT_UNDERLINE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")


def parser():
    result = argparse.ArgumentParser(description="校验实际使用短跑覆盖记录")
    result.add_argument("--repo", required=True, help="仓根的绝对路径")
    result.add_argument(
        "--scope", required=True, nargs="+", help="功能 id 列表，或单独写 全部"
    )
    result.add_argument("--review", required=True, choices=("变过的", "全部"))
    result.add_argument(
        "--usage-coverage", required=True, help="usage-coverage.json 路径"
    )
    return result


def _field_errors(value, expected, label):
    if not isinstance(value, dict):
        return [f"{label}必须是对象"]
    problems = [f"{label}缺少字段 {key}" for key in sorted(expected - value.keys())]
    problems.extend(f"{label}有多余键 {key}" for key in sorted(value.keys() - expected))
    return problems


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


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


def _strip_markdown_container_prefixes(text):
    remaining = text
    while True:
        list_item = re.match(r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)", remaining)
        if list_item is None:
            return remaining
        remaining = remaining[list_item.end() :]


def _markdown_code_lines(file_lines, excluded_lines):
    code_lines = set()
    fence_character = None
    fence_length = 0
    for index, line in enumerate(file_lines):
        if index in excluded_lines:
            continue
        container_text = _strip_markdown_container_prefixes(line)
        if fence_character is not None:
            code_lines.add(index)
            closing = re.match(r"^ {0,3}(`+|~+)[ \t]*$", container_text)
            if (
                closing is not None
                and closing.group(1)[0] == fence_character
                and len(closing.group(1)) >= fence_length
            ):
                fence_character = None
                fence_length = 0
            continue
        if container_text.startswith("    ") or container_text.startswith("\t"):
            code_lines.add(index)
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})", container_text)
        if opening is not None:
            code_lines.add(index)
            fence_character = opening.group(1)[0]
            fence_length = len(opening.group(1))
    return code_lines


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
        if (
            divider_cells is None
            or header_cells is None
            or not all(re.fullmatch(r":?-+:?", cell) for cell in divider_cells)
        ):
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


def _markdown_headings(lines, code_lines):
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
        if (
            not title
            or MARKDOWN_HEADING.match(lines[index])
            or not SETEXT_UNDERLINE.fullmatch(underline)
        ):
            continue
        level = 1 if underline.lstrip().startswith("=") else 2
        headings.append((index, level, title, index + 1))
    headings.sort(key=lambda item: item[0])
    return headings


def _actual_usage_bounds(headings, line_count):
    section = next((heading for heading in headings if "实际使用" in heading[2]), None)
    if section is None:
        return None
    heading_index, heading_level, _title, heading_end = section
    section_end = next(
        (
            index
            for index, level, _title, _end in headings
            if index > heading_index and level <= heading_level
        ),
        line_count,
    )
    return heading_end + 1, section_end


def _source_tables(text, body_start, section_end):
    tables = []
    for table in _markdown_tables(text):
        table_header = table["start"] - 1
        if body_start <= table_header and table["end"] <= section_end:
            tables.append(table)
    return tables


def _markdown_list_lines(lines, start, end):
    list_lines = set()
    inside_list = False
    for index in range(start, end):
        line = lines[index]
        if re.match(r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)", line):
            list_lines.add(index)
            inside_list = True
        elif not line.strip():
            continue
        elif inside_list and (line.startswith(" ") or line.startswith("\t")):
            list_lines.add(index)
        else:
            inside_list = False
    return list_lines


def _paragraph_source_ranges(lines, code_lines, body_start, section_end):
    list_lines = _markdown_list_lines(lines, body_start, section_end)
    sources = []
    paragraph_start = None
    paragraph_end = None
    for index in range(body_start, section_end):
        line = lines[index]
        if (
            index in code_lines
            or index in list_lines
            or not line.strip()
            or MARKDOWN_HEADING.match(line)
        ):
            if paragraph_start is not None:
                sources.append(f"README.md:{paragraph_start + 1}-{paragraph_end + 1}")
                paragraph_start = paragraph_end = None
            continue
        if paragraph_start is None:
            paragraph_start = index
        paragraph_end = index
    if paragraph_start is not None:
        sources.append(f"README.md:{paragraph_start + 1}-{paragraph_end + 1}")
    return sources


def _usage_source_ranges(repository):
    readme_path = Path(repository) / "README.md"
    try:
        text = readme_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return [], [f"无法读取 README.md 的「实际使用」来源：{error}"]

    lines = text.splitlines()
    code_lines = _markdown_code_lines(lines, set())
    bounds = _actual_usage_bounds(_markdown_headings(lines, code_lines), len(lines))
    if bounds is None:
        return [], ["README.md 缺少「实际使用」一节"]
    body_start, section_end = bounds

    # Tables and prose are the supported source formats for this README section.
    tables = _source_tables(text, body_start, section_end)
    if tables:
        return [
            f"README.md:{line_number}-{line_number}"
            for table in tables
            for line_number, _cells in table["rows"][2:]
        ], []

    return _paragraph_source_ranges(lines, code_lines, body_start, section_end), []


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
        code_files.update(
            path
            for path in feature_data["files"]
            if file_category_with_skip(repository, path, skip_paths) == "code"
        )
    return code_files


def _read_file_lines(path):
    try:
        return path.read_bytes().splitlines()
    except OSError:
        return None


def _scenario_problems(scenario, label):
    problems = _field_errors(scenario, {"command", "ran", "why_not"}, label)
    if problems or not isinstance(scenario, dict):
        return problems, False
    if not _nonempty(scenario.get("command")):
        problems.append(f"{label}.command 必须是非空字符串")
    ran = scenario.get("ran")
    why_not = scenario.get("why_not")
    if type(ran) is not bool:
        problems.append(f"{label}.ran 必须是布尔值")
        return problems, False
    if ran:
        if why_not != "":
            problems.append(f"{label}.ran 为 true 时 why_not 必须为空字符串")
        return problems, True
    if not _nonempty(why_not):
        problems.append(f"{label}.ran 为 false 时 why_not 必须说明原因")
    return problems, False


def _source_problems(source, index, expected_sources, label):
    source_label = f"{label}.sources[{index}]"
    problems = _field_errors(
        source, {"source", "scenarios", "why_none", "uses"}, source_label
    )
    if problems or not isinstance(source, dict):
        return problems, False

    problems.extend(
        _source_reference_problems(
            source.get("source"), index, expected_sources, source_label
        )
    )
    problems.extend(_source_use_problems(source.get("uses"), source_label))
    scenario_errors, any_ran = _source_scenario_problems(source, source_label)
    problems.extend(scenario_errors)
    return problems, any_ran


def _source_reference_problems(source_value, index, expected_sources, source_label):
    problems = []
    if not isinstance(source_value, str):
        problems.append(f"{source_label}.source 必须是字符串")
    elif index >= len(expected_sources):
        problems.append(
            f"{source_label}.source 没有对应的 README.md 来源：{source_value}"
        )
    elif source_value != expected_sources[index]:
        problems.append(
            f"{source_label}.source 必须恰好对应 {expected_sources[index]}，实际为 {source_value}"
        )
    return problems


def _source_use_problems(uses, source_label):
    problems = []
    if not isinstance(uses, list) or not uses:
        problems.append(f"{source_label}.uses 必须是非空字符串数组")
    else:
        for uses_index, use in enumerate(uses):
            if not _nonempty(use):
                problems.append(f"{source_label}.uses[{uses_index}] 必须是非空字符串")
    return problems


def _source_scenario_problems(source, source_label):
    problems = []
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

    any_ran = False
    for scenario_index, scenario in enumerate(scenarios):
        scenario_label = f"{source_label}.scenarios[{scenario_index}]"
        scenario_errors, ran = _scenario_problems(scenario, scenario_label)
        problems.extend(scenario_errors)
        any_ran = any_ran or ran
    return problems, any_ran


def _sources_problems(sources, expected_sources, label):
    problems = []
    if not isinstance(sources, list):
        return [f"{label}.sources 必须是数组"], False
    if len(sources) < len(expected_sources):
        problems.append(
            f"{label}.sources 缺少 README.md「实际使用」来源：应有 {len(expected_sources)} 条，实有 {len(sources)} 条"
        )
    elif len(sources) > len(expected_sources):
        problems.append(
            f"{label}.sources 多出 README.md「实际使用」来源：应有 {len(expected_sources)} 条，实有 {len(sources)} 条"
        )

    any_ran = False
    for index, source in enumerate(sources):
        source_errors, ran = _source_problems(source, index, expected_sources, label)
        problems.extend(source_errors)
        any_ran = any_ran or ran
    return problems, any_ran


def _missed_range_problems(path, missed, code_files, repository, label):
    problems = []
    file_lines = (
        _read_file_lines(Path(repository) / path) if path in code_files else None
    )
    previous_end = None
    for index, line_range in enumerate(missed):
        range_label = f"{label}.missed[{index}]"
        parsed = _usage_line_range(line_range)
        if parsed is None:
            problems.append(
                f"{range_label} 必须写成有效的 起始行-结束行：{line_range!r}"
            )
            continue
        start_line, end_line = parsed
        if file_lines is None or end_line > len(file_lines):
            count = 0 if file_lines is None else len(file_lines)
            problems.append(
                f"{range_label} 超出文件范围：{path}:{line_range}（共 {count} 行）"
            )
            continue
        if previous_end is not None and start_line <= previous_end + 1:
            problems.append(
                f"{range_label} 必须按行序排列，且相邻的 missed 行应合并成一个范围"
            )
        previous_end = end_line
    return problems


def _validate_file_entries(files, code_files, any_scenario_ran, repository, label):
    problems = []
    if not isinstance(files, dict):
        return [f"{label}.files 必须是对象"], set()
    if not any_scenario_ran and files:
        problems.append("没有 ran 为 true 的场景时 files 必须是空对象")
    for path, entry in files.items():
        file_label = f"{label}.files[{path}]"
        if path not in code_files:
            problems.append(f"{file_label} 不属于本次审查范围内的直属代码文件")
        entry_problems = _field_errors(entry, {"missed"}, file_label)
        problems.extend(entry_problems)
        if entry_problems or not isinstance(entry, dict):
            continue
        missed = entry.get("missed")
        if not isinstance(missed, list):
            problems.append(f"{file_label}.missed 必须是数组")
            continue
        problems.extend(
            _missed_range_problems(path, missed, code_files, repository, file_label)
        )
    return problems, set(files)


def _validate_unmeasured(unmeasured, code_files, label):
    problems = []
    if not isinstance(unmeasured, list):
        return [f"{label}.unmeasured 必须是数组"], set()
    paths = []
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
            problems.append(
                f"{item_label}.path 不属于本次审查范围内的直属代码文件：{path}"
            )
        if not _nonempty(item.get("why")):
            problems.append(f"{item_label}.why 必须是非空字符串")
        paths.append(path)
    for path in sorted(set(paths)):
        if paths.count(path) > 1:
            problems.append(f"{label}.unmeasured 重复文件：{path}")
    return problems, set(paths)


@dataclass
class FileCoverageContext:
    data: dict
    repository: Path
    feature_ids: list
    tracked_files: set
    any_ran: bool


def _validate_file_coverage(coverage, context):
    label = "usage-coverage"
    code_files = _direct_code_files(
        context.data,
        context.feature_ids,
        context.repository,
        context.tracked_files,
    )
    file_problems, measured = _validate_file_entries(
        coverage.get("files"),
        code_files,
        context.any_ran,
        context.repository,
        label,
    )
    unmeasured_problems, unmeasured = _validate_unmeasured(
        coverage.get("unmeasured"), code_files, label
    )
    file_problems.extend(unmeasured_problems)
    for path in sorted(code_files):
        if (path in measured) + (path in unmeasured) != 1:
            file_problems.append(
                f"直属代码文件必须恰好出现在 files 或 unmeasured：{path}"
            )
    return file_problems


def _validate_usage_coverage(coverage, data, repository, feature_ids, tracked_files):
    label = "usage-coverage"
    problems = _field_errors(coverage, {"sources", "files", "unmeasured"}, label)
    if not isinstance(coverage, dict):
        return problems

    expected_sources, source_problems = _usage_source_ranges(repository)
    problems.extend(source_problems)
    source_errors, any_ran = _sources_problems(
        coverage.get("sources"), expected_sources, label
    )
    problems.extend(source_errors)
    problems.extend(
        _validate_file_coverage(
            coverage,
            FileCoverageContext(data, repository, feature_ids, tracked_files, any_ran),
        )
    )
    return problems


def main(argv):
    options = parser().parse_args(argv)
    coverage_path = Path(options.usage_coverage)
    if not coverage_path.is_file():
        print("--usage-coverage 必须指向已有文件", file=sys.stderr)
        return 2
    try:
        coverage = json.loads(
            coverage_path.read_text(encoding="utf-8"),
            object_pairs_hook=object_without_duplicate_keys,
        )
    except (OSError, UnicodeError, ValueError) as error:
        print(f"usage-coverage 不是合法 JSON：{error}", file=sys.stderr)
        return 2

    try:
        data, _values, _records, planned, _approval_commits, _head = plan_data(
            options.repo,
            options.scope,
            options.review,
        )
        feature_ids = [feature_id for feature_id, _reason in planned]
        problems = _validate_usage_coverage(
            coverage,
            data,
            options.repo,
            feature_ids,
            tracked_paths(options.repo),
        )
    except ArgumentError as error:
        return report_cli_argument_error(error)
    except (OSError, UnicodeError, ValueError) as error:
        return report_cli_input_error(error)
    if problems:
        print("\n".join(problems))
        return 1
    print("usage-coverage 校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
