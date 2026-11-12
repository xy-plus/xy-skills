import json
import pathlib
import re
import sys


HANDOFF_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "xy-handoff"
sys.path.insert(0, str(HANDOFF_DIRECTORY))
from check_handoff import check_value, object_without_duplicate_keys  # noqa: E402
from audit import REPORT_ROUND_RE  # noqa: E402
from rule_references import (  # noqa: E402
    RULE_FILES, is_rule_source, numbered_rule_numbers, rule_quote_problem
)


REPO_RULE_NUMBERS = numbered_rule_numbers(RULE_FILES["xy-repo-rules"])
SECTION = {
    "items": [{
        "problem": str,
        "evidence": str,
        "quote": {"source": str, "text": str},
    }],
    "checked": str,
}
QUOTE_PROBLEMS = {
    "items": [{
        "problem": str,
        "evidence": str,
        "quote": {"source": str, "text": str},
        "suggestion": str,
    }],
    "checked": str,
}
REVIEW_MAIN = {
    "first_principles": {
        "essence": str,
        "deviations": SECTION,
        "quote_problems": QUOTE_PROBLEMS,
    },
    "hard_to_misuse": SECTION,
    "repo_rules": {str(number): SECTION for number in REPO_RULE_NUMBERS},
    "verdict": str,
}
REVIEW_RAZOR = {
    "occams_razor": {
        "keep": [{"entity": str, "requirement": str, "achieved": str, "cost": str}],
        "keep_old": [{"entity": str, "reason": str}],
        "delete": {"items": [{"entity": str, "loss": str}], "checked": str},
        "ask_user": {
            "items": [{"entity": str, "now": str, "change_to": str, "saving": str}],
            "checked": str,
        },
        "quote_problems": QUOTE_PROBLEMS,
    },
    "verdict": str,
}
DIAGNOSIS = {"diagnosis": str, "actions": [str]}
REPORTS = {
    "review-main.json": ("主审报告", REVIEW_MAIN),
    "review-razor.json": ("剃刀报告", REVIEW_RAZOR),
    "diagnosis.json": ("判因报告", DIAGNOSIS),
}
VERDICTS = ("通过", "改了就过", "不通过")
DIAGNOSIS_ACTIONS = ("拆分", "重想", "问用户", "顶回")
TEMPLATE_PLACEHOLDERS = [
    placeholder
    for block in re.findall(
        r"```json\s*\n(.*?)```",
        pathlib.Path(__file__).with_name("SKILL.md").read_text(encoding="utf-8"),
        re.S,
    )
    for placeholder in re.findall(r"<([^<>]+)>", block)
]


def find_placeholders(where, value):
    """递归检查所有值，不复制 SKILL.md 中的占位符。"""
    if isinstance(value, str):
        if any(
            len(piece) >= 2 and any(piece in template for template in TEMPLATE_PLACEHOLDERS)
            for piece in re.findall(r"<([^<>]+)>", value)
        ):
            return [f"{where} 还留着模板占位符"]
        return []
    if isinstance(value, dict):
        return [problem for key, item in value.items()
                for problem in find_placeholders(f"{where}.{key}", item)]
    if isinstance(value, list):
        return [problem for index, item in enumerate(value)
                for problem in find_placeholders(f"{where}[{index}]", item)]
    return []


def read_document(text):
    """用交接检查器的重复键规则解析 JSON。"""
    return json.loads(text, object_pairs_hook=object_without_duplicate_keys)


def item_list(document, *path):
    """从结构正确的报告中取数组；结构错误由 check_value 报告。"""
    value = document
    for key in path:
        if not isinstance(value, dict):
            return []
        value = value.get(key)
    return value if isinstance(value, list) else []


def check_rule_quotes(document, report_kind):
    """核对主审与剃刀发现中引用的编号规范条款。"""
    if report_kind == "主审报告":
        sections = [
            ("first_principles.deviations", item_list(
                document, "first_principles", "deviations", "items"
            )),
            ("first_principles.quote_problems", item_list(
                document, "first_principles", "quote_problems", "items"
            )),
            ("hard_to_misuse", item_list(document, "hard_to_misuse", "items")),
        ]
        sections.extend(
            (f"repo_rules.{number}", item_list(document, "repo_rules", str(number), "items"))
            for number in REPO_RULE_NUMBERS
        )
    else:
        # 原话也可能被当作保留理由，所以剃刀引用规范时必须逐字核对。
        sections = [
            ("occams_razor.quote_problems", item_list(
                document, "occams_razor", "quote_problems", "items"
            )),
        ]
    problems = []
    for section, items in sections:
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            quote = item.get("quote")
            if not isinstance(quote, dict):
                continue
            source = quote.get("source")
            if not is_rule_source(source):
                continue
            problem = rule_quote_problem(source, quote.get("text"))
            if problem is not None:
                problems.append(
                    f"报告.{section}.items[{index}].quote.text {problem}"
                )
    return problems


def check(text, filename):
    """按文件名选报告结构并返回所有内容问题。"""
    if filename not in REPORTS:
        return ["报告文件名只能是 review-main.json、review-razor.json 或 diagnosis.json"]
    report_kind, shape = REPORTS[filename]
    try:
        document = read_document(text)
    except (ValueError, TypeError) as error:
        return [f"JSON 无效：{error}"]

    problems = check_value("报告", document, shape)
    if isinstance(document, (dict, list)):
        problems += find_placeholders("报告", document)
    if not isinstance(document, dict):
        return problems

    if report_kind in ("主审报告", "剃刀报告"):
        problems += check_rule_quotes(document, report_kind)

    if report_kind == "主审报告":
        verdict = document.get("verdict")
        if isinstance(verdict, str) and verdict not in VERDICTS:
            problems.append("报告.verdict 只能是「通过」「改了就过」或「不通过」")
        findings = []
        for label, path in (
            ("first_principles.deviations", ("first_principles", "deviations", "items")),
            ("first_principles.quote_problems", ("first_principles", "quote_problems", "items")),
            ("hard_to_misuse", ("hard_to_misuse", "items")),
        ):
            if item_list(document, *path):
                findings.append(label)
        for number in REPO_RULE_NUMBERS:
            if item_list(document, "repo_rules", str(number), "items"):
                findings.append(f"repo_rules.{number}")
        if verdict == "通过" and findings:
            problems.append("主审 verdict 写「通过」但有发现：" + "、".join(findings))
        elif verdict in ("改了就过", "不通过") and not findings:
            problems.append(f"主审 verdict 写「{verdict}」但没有发现（所有 items 都为空）")
    elif report_kind == "剃刀报告":
        verdict = document.get("verdict")
        if isinstance(verdict, str) and verdict not in VERDICTS:
            problems.append("报告.verdict 只能是「通过」「改了就过」或「不通过」")
        keep = item_list(document, "occams_razor", "keep")
        keep_old = item_list(document, "occams_razor", "keep_old")
        deleted = item_list(document, "occams_razor", "delete", "items")
        asked = item_list(document, "occams_razor", "ask_user", "items")
        quote_problems = item_list(document, "occams_razor", "quote_problems", "items")
        if not keep and not keep_old and not deleted and not asked and not quote_problems:
            problems.append("剃刀报告的清单合计至少要有一项")
        # ask_user.items 不计入剃刀报告结论。
        if verdict == "通过" and deleted:
            problems.append("剃刀 verdict 写「通过」但 delete.items 有发现")
        elif verdict in ("改了就过", "不通过") and not deleted:
            problems.append(f"剃刀 verdict 写「{verdict}」但 delete.items 为空")
    else:
        actions = document.get("actions")
        if isinstance(actions, list):
            if not actions:
                problems.append("报告.actions 不能为空")
            if any(action not in DIAGNOSIS_ACTIONS for action in actions):
                problems.append("报告.actions 只能包含「拆分」「重想」「问用户」「顶回」")
            if all(isinstance(action, str) for action in actions) and len(actions) != len(set(actions)):
                problems.append("报告.actions 不能重复")

    return problems


def diagnosis_file_problems(path):
    """Return validation problems for a diagnosis file using this report checker."""
    diagnosis_path = pathlib.Path(path)
    try:
        text = diagnosis_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"无法读取判因报告：{error}"]
    problems = check(text, "diagnosis.json")
    group_path = diagnosis_path.parent
    if re.fullmatch(r"group[1-9]\d*", group_path.name):
        problems += _formal_diagnosis_group_problems(group_path)
    return problems


def _formal_diagnosis_group_problems(group_path):
    """Require a third-round failure before a formal group can record diagnosis."""
    round3_path = group_path / "round3"
    if not round3_path.is_dir():
        return [f"{group_path}：判因报告所在组必须已有 round3"]

    problems = []
    has_failure = False
    for number in (1, 2, 3):
        round_path = group_path / f"round{number}"
        for name in ("review-main.json", "review-razor.json"):
            report_path = round_path / name
            if not report_path.is_file():
                problems.append(f"{round_path}：缺少 {name}")
                continue
            try:
                text = report_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if check(text, name):
                continue
            document = read_document(text)
            if document.get("verdict") == "不通过":
                if number == 3:
                    has_failure = True
                if not (round_path / "handling.md").is_file():
                    problems.append(f"{round_path}：{name} 结论为「不通过」，缺少 handling.md")

        if not any(
            path.is_file() and path.stat().st_size > 0
            for path in round_path.glob("submitted*")
        ):
            problems.append(f"{round_path}：至少需要一个非空 submitted 文件")

    if not has_failure:
        problems.append(f"{group_path}：round3 至少要有一份结论为「不通过」的审查报告")
    return problems


def main(argv):
    if len(argv) != 2:
        print("用法：check.py <报告路径>")
        return 2
    path = pathlib.Path(argv[1]).resolve()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(f"无法读取「{path}」：{error}")
        return 1

    problems = check(text, path.name)
    kind = REPORTS.get(path.name, (None, None))[0]
    if kind == "判因报告":
        group_path = path.parent
        group_match = re.fullmatch(r"group([1-9]\d*)", group_path.name)
        if group_match is None and not any(
                child.is_file() and REPORT_ROUND_RE.fullmatch(child.name)
                for child in group_path.iterdir()):
            problems.append("判因报告必须写在 group<G>/diagnosis.json 或含合法 report-round<N>.md 的循环目录下")
        elif group_match is not None:
            problems += _formal_diagnosis_group_problems(group_path)
    elif kind in ("主审报告", "剃刀报告"):
        round_path = path.parent
        group_path = round_path.parent
        group_match = re.fullmatch(r"group([1-9]\d*)", group_path.name)
        if group_match is None or re.fullmatch(r"round[123]", round_path.name) is None:
            problems.append("审查报告和剃刀报告必须写在 group<G>/round<R>/，G 从 1 起且不能有前导零，R 只能是 1、2、3")
    else:
        group_match = None

    if group_match is not None:
        group_number = int(group_match.group(1))
        round_numbers = set()
        for child in group_path.iterdir():
            if not child.is_dir() or not child.name.startswith("round"):
                continue
            if re.fullmatch(r"round[123]", child.name) is None:
                problems.append(f"{group_path}：轮次目录名错误，{child.name} 只能是 round1、round2、round3")
            else:
                round_numbers.add(int(child.name[5:]))
        if not round_numbers:
            problems.append(f"{group_path}：轮次编号不连续，缺少 round1")
        else:
            for number in range(1, max(round_numbers) + 1):
                if number not in round_numbers:
                    problems.append(f"{group_path}：轮次编号不连续，缺少 round{number}")
        if group_number >= 2:
            previous_diagnosis = group_path.parent / f"group{group_number - 1}" / "diagnosis.json"
            if not previous_diagnosis.is_file():
                problems.append(
                    f"{previous_diagnosis} 不存在；一组要复审到第 3 轮仍不通过、派判因写下原因后才能开新组"
                )
            else:
                diagnosis_problems = diagnosis_file_problems(previous_diagnosis)
                if diagnosis_problems:
                    problems.append(
                        f"{previous_diagnosis} 判因报告无效：" + "；".join(diagnosis_problems)
                    )

    if problems:
        print("\n".join(problems))
        return 1

    if kind in ("主审报告", "剃刀报告") and path.parent.name == "round3":
        document = read_document(text)
        if document.get("verdict") == "不通过":
            group = path.parent.parent.name
            print(f"提醒：{group} 第 3 轮仍不通过，按 xy-review 先派判因子代理写 {group}/diagnosis.json，按结论处理后再开下一组")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
