import copy
import json
import pathlib
import re
import sys
from collections import Counter


REVIEW_DIRECTORY = pathlib.Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REVIEW_DIRECTORY / "change_scan"))
sys.path.insert(0, str(REVIEW_DIRECTORY / "rule_quote_check"))
from json_validation import check_value, object_without_duplicate_keys  # noqa: E402
from scan_changes import ScanError, scan_repository  # noqa: E402
from rule_references import (  # noqa: E402
    RULE_FILES, numbered_rule_numbers, rule_quote_problem
)


REPORT_ROUND_RE = re.compile(r"report-round([1-9]\d*)\.md")
REPO_RULE_NUMBERS = numbered_rule_numbers(RULE_FILES["xy-repo-rules"])
VERDICTS = ("通过", "改了就过", "不通过")
DIAGNOSIS_ACTIONS = ("拆分", "重想", "问用户", "顶回")
CLEANUP_DECISIONS = ("改", "留", "要用户定")
WORKFLOW_SKILL_PATH = REPOSITORY_ROOT / "xy-workflow" / "SKILL.md"
REPORT_TEMPLATE_MARKER = re.compile(
    r"<!--\s*report-template:\s*([^>]+?)\s*-->\s*```json\s*\n(.*?)```",
    re.S,
)


def value_shape(value):
    if isinstance(value, dict):
        return {key: value_shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [value_shape(value[0])] if value else []
    return type(value)


def _report_templates(skill_text):
    """Map explicitly named SKILL.md templates to their report filenames."""
    templates = {}
    for match in REPORT_TEMPLATE_MARKER.finditer(skill_text):
        try:
            template = json.loads(match.group(2))
        except ValueError as error:
            raise ValueError(f"SKILL.md 报告模板 JSON 无效：{error}") from error
        filenames = [name.strip() for name in match.group(1).split(",")]
        if not filenames or any(not name.endswith(".json") for name in filenames):
            raise ValueError("SKILL.md 报告模板必须声明 JSON 报告文件名")
        for filename in filenames:
            if filename in templates:
                raise ValueError(f"SKILL.md 重复声明报告模板：{filename}")
            templates[filename] = template
    return templates


SKILL_TEXT = (REVIEW_DIRECTORY / "SKILL.md").read_text(encoding="utf-8")
REPORT_TEMPLATES = _report_templates(SKILL_TEXT)
REPORT_TEMPLATE_KINDS = {
    "review-main.json": "主审报告",
    "review-razor.json": "剃刀报告",
    "diagnosis.json": "判因报告",
    "derivation-main.json": "推导报告",
    "derivation-razor.json": "推导报告",
}
if set(REPORT_TEMPLATES) != set(REPORT_TEMPLATE_KINDS):
    raise ValueError(
        "SKILL.md 报告模板名称须恰为：" + "、".join(REPORT_TEMPLATE_KINDS)
    )

REPORT_SHAPES = {
    filename: value_shape(template)
    for filename, template in REPORT_TEMPLATES.items()
}
main_shape = REPORT_SHAPES["review-main.json"]
repo_rule_shape = next(iter(main_shape["repo_rules"].values()))
repo_rule_item_shape = main_shape["first_principles"]["deviations"]["items"]
main_shape["repo_rules"] = {
    str(number): {**repo_rule_shape, "items": repo_rule_item_shape}
    for number in REPO_RULE_NUMBERS
}
REPORTS = {
    filename: (REPORT_TEMPLATE_KINDS[filename], REPORT_SHAPES[filename])
    for filename in REPORT_TEMPLATE_KINDS
}
TEMPLATE_PLACEHOLDERS = [
    placeholder
    for template in REPORT_TEMPLATES.values()
    for block in (json.dumps(template, ensure_ascii=False),)
    for placeholder in re.findall(r"<([^<>]+)>", block)
]


def item_list(document, *path):
    """从结构正确的报告中取数组；结构错误由 check_value 报告。"""
    value = document
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, list) else []


def check_rule_quotes(document, report_kind):
    """核对主审与剃刀发现中引用的编号规范条款。"""
    if report_kind == "推导报告":
        sections = [("needs", item_list(document, "needs"), True)]
    elif report_kind == "主审报告":
        sections = [
            ("first_principles.deviations", item_list(
                document, "first_principles", "deviations", "items"
            ), False),
            ("first_principles.quote_problems", item_list(
                document, "first_principles", "quote_problems", "items"
            ), False),
            ("hard_to_misuse", item_list(document, "hard_to_misuse", "items"), False),
        ]
        sections.extend(
            (f"repo_rules.{number}", item_list(document, "repo_rules", str(number), "items"), False)
            for number in REPO_RULE_NUMBERS
        )
    else:
        # 原话也可能被当作保留理由，所以剃刀引用规范时必须逐字核对。
        sections = [
            ("occams_razor.quote_problems", item_list(
                document, "occams_razor", "quote_problems", "items"
            ), False),
        ]
    problems = []
    for section, items, direct_array in sections:
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            quote = item.get("quote")
            if not isinstance(quote, dict):
                continue
            source = quote.get("source")
            problem = rule_quote_problem(source, quote.get("text"))
            if problem is not None:
                problems.append(
                    f"报告.{section}{'[' if direct_array else '.items['}{index}].quote.text {problem}"
                )
    return problems


def is_code_review_report(path):
    """Use the author-assigned code path to identify code reviews."""
    # The dispatching author supplies this path; a report must not self-label.
    parts = pathlib.Path(path).parts
    return (
        len(parts) >= 4
        and parts[-4] == "code"
        and re.fullmatch(r"group[1-9]\d*", parts[-3]) is not None
        and re.fullmatch(r"round[123]", parts[-2]) is not None
    )


def workflow_cleanup_categories():
    """Read the category names from the workflow table used by reviewers."""
    try:
        lines = [
            line.strip() for line in WORKFLOW_SKILL_PATH.read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"无法读取 xy-workflow 清理清单：{error}") from error
    heading = "| 类 | 包括但不限于 |"
    try:
        start = lines.index(heading) + 1
    except ValueError as error:
        raise ValueError("xy-workflow/SKILL.md 找不到清理清单表") from error
    categories = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = line.split("|")
        if len(cells) < 3:
            raise ValueError("xy-workflow 清理清单表格式错误")
        category = cells[1].strip()
        if category and not re.fullmatch(r"[:\-\s]+", category):
            categories.append(category)
    if not categories or len(categories) != len(set(categories)):
        raise ValueError("xy-workflow 清理清单表没有类别或类别重复")
    return categories


def _report_shape(filename, code_review):
    _report_kind, shape = REPORTS[filename]
    if filename == "review-razor.json":
        shape = copy.deepcopy(shape)
        keep_shape = shape["occams_razor"]["keep"]
        if keep_shape:
            keep_shape[0].pop("user_visible", None)
    if filename != "review-razor.json" or code_review:
        return shape
    return {key: value for key, value in shape.items() if key != "cleanup"}


def _report_for_shape(document, filename):
    if filename != "review-razor.json" or not isinstance(document, dict):
        return document
    shaped = copy.deepcopy(document)
    razor = shaped.get("occams_razor")
    keep = razor.get("keep") if isinstance(razor, dict) else None
    if isinstance(keep, list):
        for item in keep:
            if isinstance(item, dict):
                item.pop("user_visible", None)
    return shaped


def _user_visible_problems(document):
    keep = item_list(document, "occams_razor", "keep")
    problems = []
    delegated_phrases = (
        "你先判断", "由你判断", "你来判断", "你自己判断", "你判断是否",
        "判断是否应该", "你决定是否", "由你决定", "交给你判断", "如果应该的话就",
    )
    for index, item in enumerate(keep):
        if not isinstance(item, dict):
            continue
        path = f"报告.occams_razor.keep[{index}].user_visible"
        visible = item.get("user_visible")
        if not isinstance(visible, dict):
            problems.append(f"{path} 必须是对象")
            continue
        change = visible.get("change")
        if not isinstance(change, str) or not change.strip():
            problems.append(f"{path}.change 必须是非空字符串")
            continue
        if change == "不改":
            if set(visible) != {"change"}:
                problems.append(f"{path} 为「不改」时只能有 change，不能写 decided_by")
            continue
        if "decided_by" not in visible:
            problems.append(f"{path}.decided_by 有用户可见变化时必填")
            continue
        if set(visible) != {"change", "decided_by"}:
            problems.append(f"{path} 有用户可见变化时只能有 change 和 decided_by")
            continue
        decided_by = visible.get("decided_by")
        if not isinstance(decided_by, dict) or set(decided_by) != {"source", "text"}:
            problems.append(f"{path}.decided_by 必须且只能含 source 和 text")
            continue
        source = decided_by.get("source")
        text = decided_by.get("text")
        if not isinstance(source, str) or not source.strip():
            problems.append(f"{path}.decided_by.source 必须是非空字符串")
        if not isinstance(text, str) or not text.strip():
            problems.append(f"{path}.decided_by.text 必须是非空字符串")
        elif any(phrase in text for phrase in delegated_phrases):
            problems.append(f"{path}.decided_by.text 是把判断交给代理的原话，不能作为用户直接决定")
    return problems


def _main_mapping_problems(document, report_path):
    mapping = document.get("mapping") if isinstance(document, dict) else None
    if not isinstance(mapping, dict):
        return []
    items = mapping.get("items")
    merges = mapping.get("merges")
    problems = []
    if not isinstance(items, list) or not items:
        problems.append("报告.mapping.items 不能为空")
        return problems
    if not isinstance(merges, list):
        return problems

    derivation_path = pathlib.Path(report_path).parent / "derivation-main.json"
    derivation_needs = None
    try:
        derivation = json.loads(
            derivation_path.read_text(encoding="utf-8"),
            object_pairs_hook=object_without_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        derivation = None
    if isinstance(derivation, dict) and isinstance(derivation.get("needs"), list):
        derivation_needs = {
            item.get("need") for item in derivation["needs"]
            if isinstance(item, dict) and isinstance(item.get("need"), str)
        }

    blocks_by_need = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        block = item.get("block")
        need = item.get("need")
        if not isinstance(block, str) or not block.strip():
            problems.append(f"报告.mapping.items[{index}].block 必须是非空字符串")
        if not isinstance(need, str) or not need.strip():
            problems.append(f"报告.mapping.items[{index}].need 必须是非空字符串")
            continue
        if need == "无":
            continue
        if derivation_needs is not None and need not in derivation_needs:
            problems.append(
                f"报告.mapping.items[{index}].need 必须逐字匹配同一轮 derivation-main.json 的 need，或写「无」"
            )
        if isinstance(block, str):
            blocks_by_need.setdefault(need, []).append(block)

    merge_entries = {}
    for index, merge in enumerate(merges):
        if not isinstance(merge, dict):
            continue
        need = merge.get("need")
        blocks = merge.get("blocks")
        decision = merge.get("decision")
        reason = merge.get("reason")
        if not isinstance(need, str) or not need.strip():
            problems.append(f"报告.mapping.merges[{index}].need 必须是非空字符串")
            continue
        if decision not in ("合并", "不合"):
            problems.append(f"报告.mapping.merges[{index}].decision 只能是「合并」或「不合」")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"报告.mapping.merges[{index}].reason 不能为空")
        if not isinstance(blocks, list) or any(not isinstance(block, str) for block in blocks):
            problems.append(f"报告.mapping.merges[{index}].blocks 必须是对象块名称数组")
            continue
        merge_entries.setdefault(need, []).append((index, blocks))

    multi_mapped = {
        need: blocks for need, blocks in blocks_by_need.items()
        if len(blocks) > 1
    }
    for need, blocks in multi_mapped.items():
        entries = merge_entries.get(need, [])
        if len(entries) != 1:
            problems.append(f"报告.mapping.merges 对应 need「{need}」必须恰有一条")
        elif Counter(entries[0][1]) != Counter(blocks):
            problems.append(f"报告.mapping.merges[{entries[0][0]}].blocks 必须与该 need 对应的 mapping.items 块一致")
    for need, entries in merge_entries.items():
        if need not in multi_mapped:
            for index, _blocks in entries:
                problems.append(f"报告.mapping.merges[{index}] 只能列出被两块以上 mapping.items 对上的 need")
    return problems


def _cleanup_problems(document, code_review):
    if not isinstance(document, dict):
        return []
    cleanup = document.get("cleanup")
    if not code_review:
        if "cleanup" in document:
            return ["报告.cleanup 只允许出现在 code/ 下的代码审查报告"]
        return []
    if not isinstance(cleanup, dict):
        return []
    return _cleanup_decision_problems(cleanup) + _cleanup_category_problems(cleanup)


def _cleanup_decision_problems(cleanup):
    problems = []
    for index, hit in enumerate(cleanup.get("hits", [])):
        if isinstance(hit, dict) and hit.get("decision") not in CLEANUP_DECISIONS:
            problems.append(
                f"报告.cleanup.hits[{index}].decision 只能是「改」「留」或「要用户定」"
            )
    return problems


def _cleanup_category_problems(cleanup):
    problems = []
    try:
        expected_categories = workflow_cleanup_categories()
    except ValueError as error:
        return [str(error)]
    category_rows = cleanup.get("categories", [])
    if not isinstance(category_rows, list):
        return problems
    seen = [
        row.get("category") for row in category_rows
        if isinstance(row, dict) and isinstance(row.get("category"), str)
    ]
    counts = Counter(seen)
    missing = [category for category in expected_categories if counts[category] == 0]
    extra = [category for category in seen if category not in expected_categories]
    duplicates = [category for category, count in counts.items() if count > 1]
    if missing or extra or duplicates:
        details = []
        if missing:
            details.append("缺少 " + "、".join(missing))
        if extra:
            details.append("多余 " + "、".join(dict.fromkeys(extra)))
        if duplicates:
            details.append("重复 " + "、".join(duplicates))
        problems.append(
            "报告.cleanup.categories 必须恰有 xy-workflow 清理清单的每类一条："
            + "；".join(details)
        )
    return problems


def _read_submitted_scan(scan_path):
    try:
        text = scan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return None, [f"无法读取 {scan_path}：{error}"]
    try:
        submitted = json.loads(text, object_pairs_hook=object_without_duplicate_keys)
    except (ValueError, TypeError) as error:
        return None, [f"{scan_path} 格式错误：{error}"]
    if (
        not isinstance(submitted, dict)
        or not isinstance(submitted.get("repo"), str)
        or not isinstance(submitted.get("base"), str)
    ):
        return None, [f"{scan_path} 格式错误：必须是含字符串 repo 和 base 的 JSON 对象"]
    return submitted, []


def _cleanup_hit_id_problems(expected_ids, document):
    cleanup = document.get("cleanup", {}) if isinstance(document, dict) else {}
    responses = cleanup.get("hits", []) if isinstance(cleanup, dict) else []
    response_ids = [
        hit.get("id") for hit in responses
        if isinstance(hit, dict) and isinstance(hit.get("id"), str)
    ] if isinstance(responses, list) else []
    response_counts = Counter(response_ids)
    expected_counts = Counter(expected_ids)
    missing = [hit_id for hit_id in expected_ids if response_counts[hit_id] == 0]
    extra = [hit_id for hit_id in response_ids if hit_id not in expected_counts]
    duplicates = [hit_id for hit_id, count in response_counts.items() if count > 1]
    problems = []
    if missing:
        problems.append("cleanup.hits 缺少扫描命中回应：" + "、".join(dict.fromkeys(missing)))
    if extra:
        problems.append("cleanup.hits 有多余扫描命中回应：" + "、".join(dict.fromkeys(extra)))
    if duplicates:
        problems.append("cleanup.hits 有重复回应：" + "、".join(duplicates))
    return problems


def _submitted_scan_problems(report_path, document):
    scan_path = report_path.parent / "submitted-scan.json"
    submitted, problems = _read_submitted_scan(scan_path)
    if problems:
        return problems

    # The scanner examines the whole repository to find callers and copies, then
    # reports only changed paths. Re-running here catches stale or edited handoffs.
    try:
        current = scan_repository(submitted["repo"], submitted["base"])
    except ScanError as error:
        return [f"{scan_path} 重跑扫描失败：{error}"]
    problems = []
    if current["head"] != submitted.get("head"):
        problems.append(f"{scan_path} 的 head 与仓库当前 HEAD 不一致")
    if current != submitted:
        problems.append(f"{scan_path} 与重跑扫描结果不一致")
    expected_ids = [hit["id"] for hit in current["hits"]]
    problems += _cleanup_hit_id_problems(expected_ids, document)
    return problems


def _main_verdict_problems(document):
    verdict = document.get("verdict")
    if isinstance(verdict, str) and verdict not in VERDICTS:
        return ["报告.verdict 只能是「通过」「改了就过」或「不通过」"]
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
    mapping = document.get("mapping", {})
    mapping_items = mapping.get("items", []) if isinstance(mapping, dict) else []
    mapping_merges = mapping.get("merges", []) if isinstance(mapping, dict) else []
    mapping_blocks_needing_action = any(
        isinstance(item, dict) and item.get("need") == "无"
        for item in mapping_items
    ) or any(
        isinstance(merge, dict) and merge.get("decision") == "合并"
        for merge in mapping_merges
    )
    if mapping_blocks_needing_action:
        findings.append("mapping")
    if verdict == "通过" and mapping_blocks_needing_action:
        return ["mapping 有「无」或「合并」，主审 verdict 不能判通过"]
    if verdict == "通过" and findings:
        return ["主审 verdict 写「通过」但有发现：" + "、".join(findings)]
    if verdict in ("改了就过", "不通过") and not findings:
        return [f"主审 verdict 写「{verdict}」但没有发现（所有 items 都为空）"]
    return []


def _razor_verdict_problems(document):
    verdict = document.get("verdict")
    if isinstance(verdict, str) and verdict not in VERDICTS:
        return ["报告.verdict 只能是「通过」「改了就过」或「不通过」"]
    deleted = item_list(document, "occams_razor", "delete", "items")
    cleanup_hits = item_list(document, "cleanup", "hits")
    problems = []
    changed_hits = any(
        isinstance(hit, dict) and hit.get("decision") == "改"
        for hit in cleanup_hits
    )
    has_findings = bool(deleted) or changed_hits
    if verdict == "通过" and has_findings:
        problems.append("剃刀 verdict 写「通过」但 delete.items 有发现或 cleanup.hits 有「改」")
    elif verdict in ("改了就过", "不通过") and not has_findings:
        problems.append(f"剃刀 verdict 写「{verdict}」但 delete.items 为空且 cleanup.hits 没有「改」")
    return problems


def _diagnosis_action_problems(document):
    actions = document.get("actions")
    if not isinstance(actions, list):
        return []
    problems = []
    if not actions:
        problems.append("报告.actions 不能为空")
    if any(action not in DIAGNOSIS_ACTIONS for action in actions):
        problems.append("报告.actions 只能包含「拆分」「重想」「问用户」「顶回」")
    if all(isinstance(action, str) for action in actions) and len(actions) != len(set(actions)):
        problems.append("报告.actions 不能重复")
    return problems


def _placeholder_problems(document):
    if not isinstance(document, (dict, list)):
        return []
    serialized = json.dumps(document, ensure_ascii=False)
    if any(
        len(piece) >= 2 and any(piece in template for template in TEMPLATE_PLACEHOLDERS)
        for piece in re.findall(r"<([^<>]+)>", serialized)
    ):
        return ["报告有模板占位符"]
    return []


def _parse_document(text):
    try:
        return json.loads(text, object_pairs_hook=object_without_duplicate_keys), []
    except (ValueError, TypeError) as error:
        return None, [f"JSON 无效：{error}"]


def _check_parsed_document(document, filename, report_path):
    if filename not in REPORTS:
        return ["报告文件名只能是 " + "、".join(REPORTS)]
    report_kind, _shape = REPORTS[filename]
    code_review = filename == "review-razor.json" and is_code_review_report(report_path)
    shape_document = _report_for_shape(document, filename)
    problems = check_value("报告", shape_document, _report_shape(filename, code_review))
    problems += _cleanup_problems(document, code_review)
    problems += _placeholder_problems(document)
    if not isinstance(document, dict):
        return problems
    if report_kind in ("主审报告", "剃刀报告", "推导报告"):
        problems += check_rule_quotes(document, report_kind)
    if report_kind == "主审报告":
        problems += _main_mapping_problems(document, report_path)
        return problems + _main_verdict_problems(document)
    if report_kind == "剃刀报告":
        problems += _user_visible_problems(document)
        return problems + _razor_verdict_problems(document)
    if report_kind == "判因报告":
        return problems + _diagnosis_action_problems(document)
    return problems


def _check_document(text, filename, report_path):
    document, problems = _parse_document(text)
    if problems:
        return problems
    return _check_parsed_document(document, filename, report_path)


def _round_derivation_problems(report_path, filename):
    derivation_name = (
        "derivation-main.json" if filename == "review-main.json"
        else "derivation-razor.json"
    )
    derivation_path = pathlib.Path(report_path).parent / derivation_name
    try:
        text = derivation_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"该轮报告缺少或无法读取对应推导文件 {derivation_path}：{error}"]
    problems = _check_document(text, derivation_name, derivation_path)
    return [f"{derivation_path}：{problem}" for problem in problems]


def check(text, filename, report_path):
    """按 SKILL.md 声明的文件名选模板并校验报告。"""
    problems = _check_document(text, filename, report_path)
    if filename in ("review-main.json", "review-razor.json"):
        problems += _round_derivation_problems(report_path, filename)
    return problems


def diagnosis_file_problems(path):
    """Return validation problems for a diagnosis file using this report checker."""
    diagnosis_path = pathlib.Path(path)
    try:
        text = diagnosis_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"无法读取判因报告：{error}"]
    document, problems = _parse_document(text)
    if not problems:
        problems = _check_parsed_document(document, "diagnosis.json", diagnosis_path)
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
            report_problems, failed_in_round3 = _history_report_problems(
                round_path, name, number
            )
            problems.extend(report_problems)
            has_failure = has_failure or failed_in_round3
        if not _has_submitted_file(round_path):
            problems.append(f"{round_path}：至少需要一个非空 submitted 文件")

    if not has_failure:
        problems.append(f"{group_path}：round3 至少要有一份结论为「不通过」的审查报告")
    return problems


def _history_report_problems(round_path, name, number):
    report_path = round_path / name
    if not report_path.is_file():
        return [
            f"{round_path}：缺少 {name}。上一轮有不通过时，下一轮主审、剃刀都要复审"
        ], False
    try:
        text = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"{report_path}：无法读取必需报告：{error}"], False
    document, report_problems = _parse_document(text)
    if not report_problems:
        report_problems = _check_parsed_document(document, name, report_path)
        if name in ("review-main.json", "review-razor.json"):
            report_problems += _round_derivation_problems(report_path, name)
    if report_problems:
        return [f"{report_path}：{problem}" for problem in report_problems], False
    failed = document.get("verdict") == "不通过"
    if failed and not (round_path / "handling.md").is_file():
        return [f"{round_path}：{name} 结论为「不通过」，缺少 handling.md"], number == 3
    return [], failed and number == 3


def _has_submitted_file(round_path):
    return any(
        path.is_file() and path.stat().st_size > 0
        for path in round_path.glob("submitted*")
    )


def _report_location_problems(path, kind):
    problems = []
    if kind == "判因报告":
        group_path = path.parent
        group_match = re.fullmatch(r"group([1-9]\d*)", group_path.name)
        if group_match is None and not any(
            child.is_file() and REPORT_ROUND_RE.fullmatch(child.name)
            for child in group_path.iterdir()
        ):
            problems.append("判因报告必须写在 group<G>/diagnosis.json 或含合法 report-round<N>.md 的循环目录下")
        elif group_match is not None:
            problems += _formal_diagnosis_group_problems(group_path)
        return problems, group_path, group_match
    if kind in ("主审报告", "剃刀报告", "推导报告"):
        round_path = path.parent
        group_path = round_path.parent
        group_match = re.fullmatch(r"group([1-9]\d*)", group_path.name)
        if group_match is None or re.fullmatch(r"round[123]", round_path.name) is None:
            problems.append("审查报告和剃刀报告必须写在 group<G>/round<R>/，G 从 1 起且不能有前导零，R 只能是 round1、round2、round3")
        return problems, group_path, group_match
    return problems, None, None


def _round_sequence_problems(group_path):
    round_numbers = set()
    problems = []
    for child in group_path.iterdir():
        if not child.is_dir() or not child.name.startswith("round"):
            continue
        if re.fullmatch(r"round[123]", child.name) is None:
            problems.append(f"{group_path}：轮次目录名错误，{child.name} 只能是 round1、round2、round3")
        else:
            round_numbers.add(int(child.name[5:]))
    if not round_numbers:
        return problems + [f"{group_path}：轮次编号不连续，缺少 round1"]
    for number in range(1, max(round_numbers) + 1):
        if number not in round_numbers:
            problems.append(f"{group_path}：轮次编号不连续，缺少 round{number}")
    return problems


def _previous_group_problems(group_path, group_number):
    if group_number < 2:
        return []
    previous_diagnosis = group_path.parent / f"group{group_number - 1}" / "diagnosis.json"
    if not previous_diagnosis.is_file():
        return [
            f"{previous_diagnosis} 不存在；一组要复审到第 3 轮仍不通过、派判因写下原因后才能开新组"
        ]
    problems = diagnosis_file_problems(previous_diagnosis)
    if problems:
        return [f"{previous_diagnosis} 判因报告无效：" + "；".join(problems)]
    return []


def _group_sequence_problems(group_path, group_match):
    problems = _round_sequence_problems(group_path)
    problems += _previous_group_problems(group_path, int(group_match.group(1)))
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

    kind = REPORTS.get(path.name, (None, None))[0]
    document, problems = _parse_document(text)
    if not problems:
        problems = _check_parsed_document(document, path.name, path)
        if path.name in ("review-main.json", "review-razor.json"):
            problems += _round_derivation_problems(path, path.name)
        if kind == "剃刀报告" and is_code_review_report(path):
            problems += _submitted_scan_problems(path, document)
    location_problems, group_path, group_match = _report_location_problems(path, kind)
    problems += location_problems
    if group_match is not None:
        problems += _group_sequence_problems(group_path, group_match)
    if problems:
        print("\n".join(problems))
        return 1

    if kind in ("主审报告", "剃刀报告") and path.parent.name == "round3":
        if document.get("verdict") == "不通过":
            group = path.parent.parent.name
            print(f"提醒：{group} 第 3 轮仍不通过，按 xy-review 先派判因子代理写 {group}/diagnosis.json，按结论处理后再开下一组")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
