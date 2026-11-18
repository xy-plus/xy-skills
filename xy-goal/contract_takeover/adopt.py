from pathlib import Path
import os

from goal_common import *


def adopt(root, directory, matched_path, goal_content, session_id, claude_pid):
    path = Path(directory).expanduser().resolve()
    parts = path.relative_to(root).parts if path.is_relative_to(root) else ()
    if len(parts) != 3 or not parts[0].startswith("-") or parts[2] != "xy-goal":
        raise GoalError(f"契约目录必须是 <根>/<以 - 开头的项目目录>/<一件事>/xy-goal：{path}")
    require_project_directory(root, path.parents[1])
    content = goal_content if path == matched_path else (path / "goal.md").read_bytes()
    contract = Contract(path, content)
    if contract.metadata["session_id"] == session_id:
        # --resume 沿用会话 id。同会话只刷新 PID，不领取或改动其他内容。
        contract.metadata["claude_pid"] = str(claude_pid)
        write_files({path / "goal.md": encode_fields(contract.metadata)}, [])
    else:
        # 本会话已在入口核过；新会话接管还须核旧所有者的重复绑定。
        find_contract(root, contract.metadata["session_id"])
        try:
            os.kill(int(contract.metadata["claude_pid"]), 0)
        except ProcessLookupError:
            pass
        except PermissionError:
            raise GoalError("原会话进程还在：先在原会话 CronDelete 并结束它")
        else:
            raise GoalError("原会话进程还在：先在原会话 CronDelete 并结束它")
        inbox = root / session_project(session_id) / "xy-goal-inbox" / session_id
        if inbox.exists():
            require_project_directory(root, inbox.parents[1])
        quotes, reasons = incoming_quotes(inbox, path, max(contract.quotes, default=0))
        contract.metadata.update(session_id=session_id, claude_pid=str(claude_pid))
        contract.dismissed.update(reasons)
        changes = {path / "quotes" / f"{number}.md": text for number, text in quotes.items()}
        changes[path / "goal.md"] = encode_fields(contract.metadata)
        changes[path / "links.md"] = encode_links(contract.links, contract.dismissed)
        with consume_inputs([inbox] if inbox.exists() else []):
            write_files(changes, [])
    print(timer_prompt(path))
    return 0
