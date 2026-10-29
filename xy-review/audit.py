#!/usr/bin/env python3
"""限制非正式改—测循环；正式报告结构由 check.py 检查。"""
import pathlib
import re
import sys


REPORT_ROUND_RE = re.compile(r"report-round([1-9]\d*)\.md")


def audit(directory):
    """返回非正式循环中达到三轮上限、但尚无判因记录的问题。"""
    root = pathlib.Path(directory)
    reports_by_directory = {}
    for report_path in root.rglob("report-round*.md"):
        matched = REPORT_ROUND_RE.fullmatch(report_path.name)
        if report_path.is_file() and matched is not None:
            reports_by_directory.setdefault(report_path.parent, []).append(int(matched.group(1)))

    problems = []
    for directory_path, numbers in sorted(reports_by_directory.items()):
        last_number = max(numbers)
        if last_number >= 3 and not (directory_path / "diagnosis.json").is_file():
            problems.append(
                f"{directory_path}：report-round{last_number}.md 已达到非正式循环上限 3，缺少 diagnosis.json"
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
