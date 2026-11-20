from pathlib import Path
from dataclasses import dataclass
import json
import re
import sys

from goal_common import (
    Contract,
    GoalError,
    automatic_reason,
    encode_links,
    find_contract,
    inbox_directory,
    merge_inbox,
    quote_files,
    read_inbox,
    read_text,
    require_name,
    require_project_directory,
    require_project_name,
    require_text,
    write_files,
)


class HookRecordError(GoalError):
    """原话已完整写入或有残缺文件；消息携带最终记录结果与原始错误。"""


@dataclass(frozen=True)
class WrittenQuote:
    number: int
    path: Path
    text: str


def hook_input(command):
    try:
        payload = json.load(sys.stdin)
    except (ValueError, UnicodeError) as error:
        raise GoalError(f"hook 输入必须是 JSON 对象：{error}") from error
    if not isinstance(payload, dict):
        raise GoalError("hook 输入必须是 JSON 对象")
    if "agent_id" in payload:
        return None
    session_id = payload.get("session_id")
    require_name(session_id, "hook session_id")
    if command == "hook-prompt":
        text = payload.get("prompt")
        if not isinstance(text, str):
            raise GoalError("hook 输入缺少字符串 prompt")
    else:
        if payload.get("tool_name") != "AskUserQuestion":
            raise GoalError("hook-answer 只接收 AskUserQuestion")
        if "tool_input" not in payload or "tool_response" not in payload:
            raise GoalError("hook-answer 缺少 tool_input 或 tool_response")
        # tool_response 格式没有官方保证：只序列化原值，不提取答案或重述问题。
        text = json.dumps({"tool_input": payload["tool_input"], "tool_response": payload["tool_response"]},
                          ensure_ascii=False, indent=2)
    return session_id, text, payload.get("transcript_path")

def record_hook(root, session_id, text, transcript_path):
    found = find_contract(root, session_id)
    if found is None:
        path, contract, directory, number = inbox_record_target(root, session_id, transcript_path)
    else:
        path, contract, directory, number, validation_error = contract_record_target(
            root, session_id, text, found)
    quote = write_quote(directory, number, text)
    if path is not None:
        classify_recorded_quote(path, contract, validation_error,
                                WrittenQuote(number, quote, text))


def inbox_record_target(root, session_id, transcript_path):
    require_text(transcript_path, "hook transcript_path")
    transcript = Path(transcript_path)
    if not transcript.is_absolute():
        raise GoalError("hook transcript_path 必须是绝对路径")
    project_name = require_project_name(transcript.parent.name)
    project = root / project_name
    project.mkdir(exist_ok=True)
    require_project_directory(root, project)
    directory = inbox_directory(root, project, session_id, create=True)
    quotes = read_inbox(root, project, session_id)
    return None, None, directory, max(quotes, default=0) + 1


def contract_record_target(root, session_id, text, found):
    path, goal_content = found
    require_project_directory(root, path.parents[1])
    validation_error = None
    try:
        contract = Contract(path, goal_content)
    except (GoalError, OSError) as error:
        contract = None
        validation_error = error
    if contract is not None:
        try:
            contract = merge_inbox(contract, session_id)
        except (GoalError, OSError) as error:
            raise HookRecordError(f"暂存原话并入失败；本次输入尚未记录。原始错误：{error}") from error
        return path, contract, path / "quotes", max(contract.quotes, default=0) + 1, None
    try:
        quotes = quote_files(path / "quotes")
        links_text = read_text(path / "links.md")
    except (GoalError, OSError):
        stage_corrupt_contract_input(root, path, session_id, text, validation_error)
    # 与 parse_links 一样按 splitlines 分行，跳过所有行首编号，避免悬空映射认领新原话。
    reserved = {int(match[0]) for line in links_text.splitlines()
                if (match := re.match(r"[0-9]+", line)) is not None}
    number = max(quotes.keys() | reserved, default=0) + 1
    return path, None, path / "quotes", number, validation_error


def write_quote(directory, number, text):
    quote = directory / f"{number}.md"
    return write_new_quote(quote, text)


def write_new_quote(quote, text, staged=False):
    output = quote.open("x", encoding="utf-8", newline="")
    try:
        with output:
            output.write(text)
    except BaseException as error:
        try:
            quote.unlink()
        except Exception as cleanup_error:
            prefix = "暂存原话" if staged else "这次输入的原话文件"
            detail = (str(error) if staged or isinstance(error, GoalError)
                      else f"{type(error).__name__}: {error}")
            raise HookRecordError(
                f"{prefix}可能残缺（{quote}），先删掉它。原始错误：{detail}；"
                f"删除失败：{type(cleanup_error).__name__}: {cleanup_error}") from error
        raise
    return quote


def classify_recorded_quote(path, contract, validation_error, record):
    # 原话完整写入就是提交点。校验或分类失败都留给后续处理，不能再删除原话。
    try:
        if validation_error is not None:
            raise validation_error
        reason = automatic_reason(record.text, path)
        if reason is not None:
            contract.dismissed[record.number] = reason
            write_files({contract.path / "links.md": encode_links(contract.links, contract.dismissed)}, [])
    except Exception as error:
        detail = str(error) if isinstance(error, GoalError) else f"{type(error).__name__}: {error}"
        raise HookRecordError(
            f"这次输入已记为原话 {record.number}（{record.path}）。原始错误：{detail}") from error


def stage_corrupt_contract_input(root, contract_path, session_id, text, cause):
    project = contract_path.parents[1]
    require_project_directory(root, project)
    inbox = inbox_directory(root, project, session_id, create=True)
    quote = inbox / f"{max(read_inbox(root, project, session_id), default=0) + 1}.md"
    write_new_quote(quote, text, staged=True)
    detail = str(cause) if cause is not None else "契约原话或关系文件不可读"
    raise HookRecordError(
        f"这次输入已暂存，修好契约后按原序并入。暂存位置：{quote}。原始错误：{detail}")
