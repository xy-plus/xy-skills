import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from goal_common import (
    HOOK_COMMANDS,
    TASK_FIELDS,
    Contract,
    GoalError,
    find_contract,
    inbox_directory,
    incoming_quotes,
    merge_inbox,
    publish_contract,
    require_name,
    require_pid,
    require_project_directory,
    require_text,
    root_lock,
    session_project,
    timer_prompt,
)
from capture_quotes.recording import HookRecordError, hook_input, record_hook
from track_and_verify.operations import (
    check,
    change,
    contract_fingerprint,
    relink,
    show,
    validate_close_review,
)
from contract_takeover.adopt import adopt as adopt_contract


@dataclass(frozen=True)
class ExecutionContext:
    root: Path
    session_id: str
    claude_pid: int | None
    now: datetime


def parser():
    result = argparse.ArgumentParser()
    result.add_argument("--root", required=True)
    commands = result.add_subparsers(dest="command", required=True)
    opening = commands.add_parser("open")
    opening.add_argument("slug")
    opening.add_argument("target")
    for name in ("show", "check"):
        commands.add_parser(name)
    closing = commands.add_parser("close")
    closing.add_argument("--review", required=True)
    fingerprint = commands.add_parser("fingerprint")
    fingerprint.add_argument("directory")
    for name in HOOK_COMMANDS:
        commands.add_parser(name)
    adopting = commands.add_parser("adopt")
    adopting.add_argument("directory")
    for command in (opening, adopting):
        command.add_argument("--claude-pid", type=int, required=True,
                             help='在 Claude Code 的 Bash 中用 "$PPID" 显式传入本会话进程号')
    adding = commands.add_parser("add")
    for name in TASK_FIELDS.values():
        adding.add_argument(f"--{name}", required=True)
    adding.add_argument("--source", type=int, nargs="+", required=True)
    adding.add_argument("--source-kind", choices=("原话直提", "作者发起"), required=True)
    adding.add_argument("--evidence")
    updating = commands.add_parser("update")
    updating.add_argument("task")
    for name in TASK_FIELDS.values():
        updating.add_argument(f"--{name}")
    updating.add_argument("--source", type=int, nargs="+")
    relinking = commands.add_parser("relink")
    relinking.add_argument("task", nargs="?")
    relinking.add_argument("--source", type=int, nargs="+")
    dismissing = commands.add_parser("dismiss")
    dismissing.add_argument("quote", type=int)
    dismissing.add_argument("--reason", required=True)
    accepting = commands.add_parser("accept")
    accepting.add_argument("task")
    accepting.add_argument("--verdict", required=True)
    cancelling = commands.add_parser("cancel")
    cancelling.add_argument("task")
    cancel_basis = cancelling.add_mutually_exclusive_group(required=True)
    cancel_basis.add_argument("--quote", type=int)
    cancel_basis.add_argument("--reason")
    reopening = commands.add_parser("reopen")
    reopening.add_argument("task")
    reopening.add_argument("--state", required=True)
    reopening.add_argument("--pointer", required=True)
    return result


def execute(options, *, root, session_id, claude_pid, now):
    context = ExecutionContext(root, session_id, claude_pid, now)
    if options.command == "fingerprint":
        return print_contract_fingerprint(options.directory, root)
    found = find_contract(root, session_id)
    if options.command in ("open", "adopt"):
        return execute_start_command(options, context, found)
    if found is None:
        raise GoalError("本会话没有契约")
    path, goal_content = found
    if options.command not in ("show", "check"):
        require_project_directory(root, path.parents[1])
    return execute_contract_command(
        options, path=path, goal_content=goal_content, session_id=context.session_id)


def print_contract_fingerprint(directory, root):
    requested = Path(directory)
    if not requested.is_absolute():
        raise GoalError("契约目录必须是绝对路径")
    path = requested.resolve(strict=True)
    try:
        parts = path.relative_to(root).parts
    except ValueError as error:
        raise GoalError(f"契约目录必须在 RUNS_ROOT 下：{path}") from error
    if len(parts) != 3 or not parts[0].startswith("-") or parts[2] != "xy-goal":
        raise GoalError(f"契约目录必须是 <根>/<以 - 开头的项目目录>/<一件事>/xy-goal：{path}")
    require_project_directory(root, path.parents[1])
    print(contract_fingerprint(path, (path / "goal.md").read_bytes()))
    return 0


def execute_start_command(options, context, found_contract):
    path = found_contract[0] if found_contract is not None else None
    if path is not None and options.command not in ("show", "check"):
        require_project_directory(context.root, path.parents[1])
    require_pid(context.claude_pid)
    if path is not None and not (options.command == "adopt"
            and Path(options.directory).expanduser().resolve() == path):
        raise GoalError(f"本会话已有契约：{path}")
    if options.command == "open":
        return open_contract(options, context)
    return adopt_contract(context.root, options.directory, found_contract,
                          context.session_id, context.claude_pid)


def open_contract(options, context):
    require_name(options.slug, "slug")
    require_text(options.target, "目标")
    project = context.root / session_project(context.session_id)
    path = project / f"{context.now:%Y%m%d-%H%M%S}-{options.slug}" / "xy-goal"
    inbox = inbox_directory(context.root, project, context.session_id)
    quotes, reasons = incoming_quotes(
        context.root, project, context.session_id, path, 0)
    metadata = {"target": options.target, "session_id": context.session_id,
                "claude_pid": str(context.claude_pid)}
    links = "".join(f"{number} 不必跟进 {reason}\n" for number, reason in reasons.items())
    publish_contract(context.root, path, metadata=metadata, quotes=quotes, links=links,
                     input_paths=[inbox] if inbox is not None else [])
    print(timer_prompt(path))
    return 0


def execute_contract_command(options, *, path, goal_content, session_id):
    if options.command == "relink":
        return relink(path, goal_content, options.task, options.source)
    contract = Contract(path, goal_content)
    if options.command == "show":
        show(contract)
    elif options.command == "check":
        return check(contract, session_id)
    elif options.command == "close":
        contract = merge_inbox(contract, session_id)
        if contract.tasks:
            raise GoalError("仍有开放事项：" + "、".join(contract.tasks))
        pending = contract.pending()
        if pending:
            raise GoalError("仍有还没处理的原话：" + "、".join(map(str, pending)))
        validate_close_review(contract, options.review)
        # 子代理整体复核、CronDelete 和汇报由 agent 按 SKILL.md 完成。
        (contract.path / "goal.md").unlink()
    else:
        change(contract, options)
    return 0



def main(arguments, *, session_id, now):
    """根目录与进程号都从命令参数取；会话和时钟显式传入。"""
    options = None
    try:
        options = parser().parse_args(arguments)
        require_text(options.root, "--root")
        root = Path(options.root)
        if not root.is_absolute():
            raise GoalError("--root 必须是绝对路径")
        # Claude Code 的 Bash 的 $PPID 是本会话 claude。由该 Bash 展开后传入，
        # 不依赖进程名、argv[0]，也不依赖未公开保证的 CLAUDE_PID 环境变量。
        claude_pid = options.claude_pid if options.command in ("open", "adopt") else None
        incoming = None
        if options.command.startswith("hook-"):
            incoming = hook_input(options.command)
            if incoming is None:
                return 0
        else:
            require_name(session_id, "CLAUDE_CODE_SESSION_ID")
        with root_lock(root):
            root = root.resolve()
            if incoming is not None:
                record_hook(root, *incoming)
                return 0
            return execute(options, root=root, session_id=session_id, claude_pid=claude_pid, now=now)
    except Exception as error:
        # Claude Code 只把退出 0 的 hook 输出（additionalContext）交给 agent：退出 1 时
        # stderr 进不了 agent 上下文，UserPromptSubmit 退出 2 还会拦下用户消息（见 README）。
        # 所以运行期 hook 失败输出 JSON、退出 0，不写 stderr；参数语法错误由标准 argparse 退出 2。
        command = options.command if options is not None else None
        if command in HOOK_COMMANDS:
            detail = str(error) if isinstance(error, GoalError) else f"{type(error).__name__}: {error}"
            record = "" if isinstance(error, HookRecordError) else "这次输入没有记入契约。原始错误："
            context = f"请优先修复本会话契约或 hook 问题，再做别的。{record}{detail}"
            event = "UserPromptSubmit" if command == "hook-prompt" else "PostToolUse"
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": event, "additionalContext": context}}, ensure_ascii=False))
            return 0
        if not isinstance(error, (GoalError, OSError, UnicodeError)):
            raise
        print(error, file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], session_id=os.environ.get("CLAUDE_CODE_SESSION_ID"), now=datetime.now()))
