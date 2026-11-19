from pathlib import Path
import json
import re
import sys

from goal_common import (
    Contract,
    GoalError,
    automatic_reason,
    encode_links,
    find_contract,
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
    validation_error = None
    if found is not None:
        path, goal_content = found
        require_project_directory(root, path.parents[1])
        contract = None
        try:
            contract = Contract(path, goal_content)
        except (GoalError, OSError) as error:
            validation_error = error
        directory = path / "quotes"
        if contract is None:
            quotes = quote_files(directory)
            # 与 parse_links 一样按 splitlines 分行，跳过所有行首编号，避免悬空映射认领新原话。
            reserved = {int(match[0]) for line in read_text(path / "links.md").splitlines()
                        if (match := re.match(r"[0-9]+", line)) is not None}
            number = max(quotes.keys() | reserved, default=0) + 1
        else:
            quotes = contract.quotes
            number = max(quotes, default=0) + 1
    else:
        path = None
        contract = None
        require_text(transcript_path, "hook transcript_path")
        transcript = Path(transcript_path)
        if not transcript.is_absolute():
            raise GoalError("hook transcript_path 必须是绝对路径")
        project_name = require_project_name(transcript.parent.name)
        project = root / project_name
        project.mkdir(exist_ok=True)
        require_project_directory(root, project)
        directory = project / "xy-goal-inbox" / session_id
        quotes = read_inbox(directory)
        directory.mkdir(parents=True, exist_ok=True)
        number = max(quotes, default=0) + 1
    quote = directory / f"{number}.md"
    output = quote.open("x", encoding="utf-8", newline="")
    try:
        with output:
            output.write(text)
    except BaseException as error:
        try:
            quote.unlink()
        except Exception as cleanup_error:
            detail = str(error) if isinstance(error, GoalError) else f"{type(error).__name__}: {error}"
            raise HookRecordError(
                f"这次输入的原话文件可能残缺（{quote}），先删掉它。原始错误：{detail}；"
                f"删除失败：{type(cleanup_error).__name__}: {cleanup_error}") from error
        raise
    # 原话完整写入就是提交点。校验或分类失败都留给后续处理，不能再删除原话。
    if path is not None:
        try:
            if validation_error is not None:
                raise validation_error
            reason = automatic_reason(text, path)
            if reason is not None:
                contract.dismissed[number] = reason
                write_files({contract.path / "links.md": encode_links(contract.links, contract.dismissed)}, [])
        except Exception as error:
            detail = str(error) if isinstance(error, GoalError) else f"{type(error).__name__}: {error}"
            raise HookRecordError(f"这次输入已记为原话 {number}（{quote}）。原始错误：{detail}") from error
