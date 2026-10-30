"""Approval summaries and verified user consent records."""
from collections import Counter
from datetime import date
import json
from pathlib import Path
import re

from .structure import structure_from_export


def approval_changes(previous, current):
    """Describe structural changes; ``None`` means the repository had no map."""
    current_structure = structure_from_export(current)
    current_details = {item["id"]: item for item in current_structure["elements"]}
    current_relations = Counter((item["source"], item["target"], item["title"])
                                for item in current_structure["relationships"])
    if previous is None:
        return {
            "first_build": True,
            "added_elements": [current_details[element_id]
                               for element_id in sorted(current_details)],
            "deleted_elements": [],
            "changed_elements": [],
            "added_relationships": sorted(current_relations.elements()),
            "deleted_relationships": [],
        }

    previous_structure = structure_from_export(previous)
    previous_details = {item["id"]: item for item in previous_structure["elements"]}
    old_ids = set(previous_details)
    new_ids = set(current_details)
    changed = []
    for element_id in sorted(old_ids & new_ids):
        old_item = previous_details[element_id]
        new_item = current_details[element_id]
        fields = [field for field in ("kind", "title", "description", "parent")
                  if old_item[field] != new_item[field]]
        if fields:
            changed.append({"id": element_id, "fields": fields,
                            "old": {field: old_item[field] for field in
                                    ("kind", "title", "description", "parent")},
                            "new": {field: new_item[field] for field in
                                    ("kind", "title", "description", "parent")}})

    old_relations = Counter((item["source"], item["target"], item["title"])
                            for item in previous_structure["relationships"])
    new_relations = current_relations
    added = sorted((new_relations - old_relations).elements())
    deleted = sorted((old_relations - new_relations).elements())
    return {
        "first_build": False,
        "added_elements": [current_details[element_id] for element_id in sorted(new_ids - old_ids)],
        "deleted_elements": [previous_details[element_id] for element_id in sorted(old_ids - new_ids)],
        "changed_elements": changed,
        "added_relationships": added,
        "deleted_relationships": deleted,
    }


def format_approval_changes(changes):
    """Render the comparison as lines suitable for the approval prompt."""
    if changes["first_build"]:
        lines = ["首次建图：以下节点和关系全部是新增。"]
    else:
        lines = []
    for item in changes["added_elements"]:
        parent = item["parent"] if item["parent"] is not None else "仓根"
        lines.append(f"新增节点：{item['id']}（类型 {item['kind']}；标题「{item['title']}」；"
                     f"描述「{item['description']}」；上级「{parent}」）")
    for item in changes["deleted_elements"]:
        parent = item["parent"] if item["parent"] is not None else "仓根"
        lines.append(f"删除节点：{item['id']}（类型 {item['kind']}；标题「{item['title']}」；"
                     f"描述「{item['description']}」；上级「{parent}」）")
    labels = {"kind": "类型", "title": "标题", "description": "描述", "parent": "上级"}
    for item in changes["changed_elements"]:
        details = "；".join(
            f"{labels[field]}「{item['old'][field]}」→「{item['new'][field]}」"
            for field in item["fields"]
        )
        lines.append(f"节点改动：{item['id']}（{details}）")
    for source, target, title in changes["added_relationships"]:
        lines.append(f"新增关系：{source} → {target}「{title}」")
    for source, target, title in changes["deleted_relationships"]:
        lines.append(f"删除关系：{source} → {target}「{title}」")
    if not lines:
        lines.append("功能结构未变化。")
    return lines


def approval_digest_line(digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("摘要必须是 64 位小写 SHA-256")
    return f"repo-map sha256 {digest}"


def _is_contract_quote(path):
    resolved = Path(path).resolve()
    parts = resolved.parts
    return (len(parts) >= 3 and parts[-3:-1] == ("xy-goal", "quotes")
            and re.fullmatch(r"[1-9][0-9]*\.md", parts[-1]) is not None)


def validate_approval_quote(path, digest):
    """Raise ValueError unless a hook quote records consent for this digest."""
    # These checks prevent accidental misuse; a local writer can forge both files and is out of scope.
    digest_line = approval_digest_line(digest)
    if not _is_contract_quote(path):
        raise ValueError("原话路径必须形如 …/xy-goal/quotes/<正整数>.md")
    try:
        with open(path, encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, ValueError) as error:
        raise ValueError(f"无法读取原话 hook 文件：{error}") from error
    if not isinstance(document, dict):
        raise ValueError("原话不是 hook 结构")
    tool_input = document.get("tool_input")
    tool_response = document.get("tool_response")
    if not isinstance(tool_input, dict) or not isinstance(tool_response, dict):
        raise ValueError("原话缺少 tool_input 或 tool_response")
    input_answers = tool_input.get("answers")
    response_answers = tool_response.get("answers")
    if not isinstance(input_answers, dict) or not isinstance(response_answers, dict):
        raise ValueError("原话 hook 缺少 answers")
    if input_answers != response_answers:
        raise ValueError("tool_input 与 tool_response 的 answers 不一致")
    questions = tool_input.get("questions")
    response_questions = tool_response.get("questions")
    if not isinstance(questions, list) or not isinstance(response_questions, list):
        raise ValueError("原话 hook 缺少 questions")

    digest_questions = []
    matching_questions = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        prompt = question.get("question")
        if not isinstance(prompt, str):
            continue
        if re.search(r"repo-map sha256 [0-9a-f]{64}", prompt):
            digest_questions.append(question)
        if digest_line in prompt:
            matching_questions.append(question)
    if digest_questions and not matching_questions:
        raise ValueError("摘要不匹配：原话提问没有包含当前功能结构摘要")
    if not matching_questions:
        raise ValueError("原话没有包含当前功能结构摘要")

    for question in matching_questions:
        prompt = question["question"]
        options = question.get("options")
        if not isinstance(options, list):
            continue
        has_approval_option = any(
            isinstance(option, dict) and option.get("label") == "同意改图"
            for option in options
        )
        if not has_approval_option:
            raise ValueError("摘要对应的问题没有「同意改图」选项")
        response_has_question = any(
            isinstance(response_question, dict)
            and response_question.get("question") == prompt
            for response_question in response_questions
        )
        if not response_has_question:
            raise ValueError("tool_response 没有对应的摘要问题")
        if input_answers.get(prompt) != "同意改图":
            raise ValueError("回答不是「同意改图」")
        return
    raise ValueError("原话没有对当前摘要给出有效同意")


def recorded_digests(approvals_path):
    path = Path(approvals_path)
    if not path.exists():
        raise ValueError(f"缺少同意记录：{path}")
    digests = set()
    pattern = re.compile(r"^repo-map sha256 ([0-9a-f]{64}) (\d{4}-\d{2}-\d{2}) (.+)$")
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = pattern.fullmatch(line)
        if match is None:
            raise ValueError(f"同意记录第 {number} 行格式错误")
        try:
            date.fromisoformat(match.group(2))
        except ValueError as error:
            raise ValueError(f"同意记录第 {number} 行日期错误") from error
        digests.add(match.group(1))
    return digests


def record_approval(repo_map, quote_path, digest, approval_date):
    """Append one approval row after validating the original hook quote."""
    approval_digest_line(digest)
    try:
        date.fromisoformat(approval_date)
    except ValueError as error:
        raise ValueError("日期必须是 YYYY-MM-DD") from error
    repo_map = Path(repo_map)
    approvals_path = repo_map / "approvals.txt"
    validate_approval_quote(quote_path, digest)
    if digest in recorded_digests(approvals_path):
        return False
    quote = str(Path(quote_path))
    line = f"{approval_digest_line(digest)} {approval_date} {quote}\n"
    with approvals_path.open("a", encoding="utf-8") as stream:
        stream.write(line)
    return True
