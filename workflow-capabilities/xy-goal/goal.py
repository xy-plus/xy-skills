import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from goal_common import (
    HOOK_COMMANDS,
    TASK_FIELDS,
    Contract,
    GoalError,
    find_contract,
    incoming_quotes,
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
from track_and_verify.operations import check, change, relink, show
from contract_takeover.adopt import adopt as adopt_contract


def parser():
    result = argparse.ArgumentParser()
    result.add_argument("--root", required=True)
    commands = result.add_subparsers(dest="command", required=True)
    opening = commands.add_parser("open")
    opening.add_argument("slug")
    opening.add_argument("target")
    for name in ("show", "check", "close"):
        commands.add_parser(name)
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
    return result


def execute(options, *, root, session_id, claude_pid, now):
    found = find_contract(root, session_id)
    path, goal_content = found if found is not None else (None, None)
    matched_path = path
    if path is not None and options.command not in ("show", "check"):
        require_project_directory(root, path.parents[1])
    if options.command in ("open", "adopt"):
        require_pid(claude_pid)
        if path is not None and not (options.command == "adopt"
                and Path(options.directory).expanduser().resolve() == path):
            raise GoalError(f"本会话已有契约：{path}")
        if options.command == "open":
            require_name(options.slug, "slug")
            require_text(options.target, "目标")
            project = root / session_project(session_id)
            path = project / f"{now:%Y%m%d-%H%M%S}-{options.slug}" / "xy-goal"
            inbox = project / "xy-goal-inbox" / session_id
            quotes, reasons = incoming_quotes(inbox, path, 0)
            metadata = {"target": options.target, "session_id": session_id, "claude_pid": str(claude_pid)}
            links = "".join(f"{number} 不必跟进 {reason}\n" for number, reason in reasons.items())
            publish_contract(root, path, metadata, quotes, links,
                             [inbox] if inbox.exists() else [])
            print(timer_prompt(path))
            return 0
        return adopt_contract(root, options.directory, matched_path, goal_content,
                              session_id, claude_pid)
    if path is None:
        raise GoalError("本会话没有契约")
    if options.command == "relink":
        return relink(path, goal_content, options.task, options.source)
    contract = Contract(path, goal_content)
    if options.command == "show":
        show(contract)
    elif options.command == "check":
        return check(contract, session_id)
    elif options.command == "close":
        if contract.tasks:
            raise GoalError("仍有开放事项：" + "、".join(contract.tasks))
        pending = contract.pending()
        if pending:
            raise GoalError("仍有还没处理的原话：" + "、".join(map(str, pending)))
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
