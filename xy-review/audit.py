#!/usr/bin/env python3
"""核对事项目录里的 xy-review 轮次结构和判因记录。"""
from collections import defaultdict
import pathlib
import re
import sys

from check import report_kind, split_sections


GROUP_RE = re.compile(r"group(\d+)")
ROUND_RE = re.compile(r"round(\d+)")
REPORT_ROUND_RE = re.compile(r"report-round([1-9]\d*)\.md")


def _round_failed(round_path):
    for report_path in sorted(round_path.glob("*.md")):
        if not report_path.is_file():
            continue
        sections, _ = split_sections(report_path.read_text(encoding="utf-8"))
        if (report_kind(sections) in ("审查报告", "剃刀报告") and "结论" in sections
                and "\n".join(sections["结论"]).strip() == "不通过"):
            return True
    return False


def _missing_ranges(numbers):
    previous = 0
    for number in sorted(numbers):
        if number > previous + 1:
            yield previous + 1, number - 1
        previous = number


def _missing_label(prefix, start, end):
    if start == end:
        return f"{prefix}{start}"
    return f"{prefix}{start} 到 {prefix}{end}"


def audit(directory):
    """返回事项目录中发现的结构问题。"""
    root = pathlib.Path(directory)
    problems = []
    groups_by_object = defaultdict(dict)
    for group_path in root.rglob("group*"):
        if not group_path.is_dir():
            continue
        matched = GROUP_RE.fullmatch(group_path.name)
        if matched is None:
            continue
        group_text = matched.group(1)
        group_number = int(group_text)
        if group_number < 1 or str(group_number) != group_text:
            problems.append(
                f"{group_path.parent}：分组编号格式错误，{group_path.name} 必须从 group1 起且不能有前导零"
            )
        else:
            groups_by_object[group_path.parent][group_number] = group_path
        for round_path in group_path.iterdir():
            round_match = ROUND_RE.fullmatch(round_path.name)
            if not round_path.is_dir() or round_match is None:
                continue
            round_text = round_match.group(1)
            round_number = int(round_text)
            if round_number < 1 or str(round_number) != round_text:
                problems.append(
                    f"{group_path}：轮次编号格式错误，{round_path.name} 必须从 round1 起且不能有前导零"
                )

    for object_path, groups in sorted(groups_by_object.items()):
        groups_with_rounds = {}
        for number, group_path in sorted(groups.items()):
            rounds = {}
            for round_path in group_path.iterdir():
                matched = ROUND_RE.fullmatch(round_path.name)
                if round_path.is_dir() and matched is not None:
                    round_text = matched.group(1)
                    round_number = int(round_text)
                    if round_number > 0 and str(round_number) == round_text:
                        rounds[round_number] = round_path
            if rounds:
                groups_with_rounds[number] = rounds

        if not groups_with_rounds:
            continue
        for start, end in _missing_ranges(groups_with_rounds):
            missing = _missing_label("group", start, end)
            problems.append(f"{object_path}：分组编号不连续，缺少 {missing}")

        for group_number, rounds in sorted(groups_with_rounds.items()):
            for start, end in _missing_ranges(rounds):
                missing = _missing_label("round", start, end)
                problems.append(f"{groups[group_number]}：轮次编号不连续，缺少 {missing}")
            later_rounds = sorted(number for number in rounds if number >= 4)
            if later_rounds:
                problems.append(
                    f"{groups[group_number]}：超过最多 3 轮的上限，出现 round{later_rounds[0]} 及以上"
                )
            last_round_number = max(rounds)
            if (last_round_number >= 3 and _round_failed(rounds[last_round_number])
                    and not (groups[group_number] / "diagnosis.md").is_file()):
                problems.append(
                    f"{groups[group_number]}：最后一轮 round{last_round_number} 结论为不通过，缺少 diagnosis.md"
                )

    report_rounds_by_directory = defaultdict(list)
    for report_path in root.rglob("report-round*.md"):
        matched = REPORT_ROUND_RE.fullmatch(report_path.name)
        if report_path.is_file() and matched is not None:
            report_rounds_by_directory[report_path.parent].append(int(matched.group(1)))
    for directory_path, numbers in sorted(report_rounds_by_directory.items()):
        last_number = max(numbers)
        if last_number >= 3 and not (directory_path / "diagnosis.md").is_file():
            problems.append(
                f"{directory_path}：report-round{last_number}.md 已达到非正式循环上限 3，缺少 diagnosis.md"
            )

    return problems


def main(argv):
    if len(argv) != 2 or not pathlib.Path(argv[1]).is_dir():
        print("用法：audit.py <目录路径>")
        return 2
    problems = audit(argv[1])
    if problems:
        print("\n".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
