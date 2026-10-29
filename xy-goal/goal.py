"""xy-goal 的唯一写入口；读取可直接用 Read/cat，映射只存 links.md。

Markdown 字段的值用 JSON 字符串编码，让换行和引号不会伪造新字段。
原话正文不经过这个编码器：quotes/N.md 保存收到的原文。
"""

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile


# 用户要求的，保留：show 只提示压短，不阻止写入或 check。
ITEM_LIMIT = 300
TOTAL_LIMIT = 5000
TASK_FIELDS = {"标题": "title", "判据": "criteria", "状态": "state", "指针": "pointer"}
GOAL_FIELDS = ("target", "session_id", "claude_pid")
HOOK_COMMANDS = ("hook-prompt", "hook-answer")
RELINK_USAGE = "；用 relink [<事项> --source <编号>…] 修复，编号看 quotes/"
LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\u0085\u2028\u2029"
PID_USAGE = ('请在 Claude Code 的 Bash 工具中运行，在 open/adopt 命令后传入 '
             '--claude-pid "$PPID"（由该 Bash 展开）')


class GoalError(ValueError):
    """可向调用者直接说明的契约或用法错误。"""


class HookRecordError(GoalError):
    """原话已完整写入或有残缺文件；消息携带最终记录结果与原始错误。"""


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        if "--claude-pid" in message:
            message += "；" + PID_USAGE
        raise GoalError(message)


def encode_fields(values):
    return "".join(f"{key}: {json.dumps(value, ensure_ascii=False)}\n"
                   for key, value in values.items())


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise GoalError(f"{label}不能为空")


def require_name(value, label):
    require_text(value, label)
    if value in (".", "..") or any(char in value for char in "/\\\n\r\x00"):
        raise GoalError(f"{label} 必须是单个目录名")


def require_state(value):
    if value != "在办" and not (value.startswith("等用户：") and value[4:].strip()):
        raise GoalError("状态只认「在办」或「等用户：待定的事」")


def read_text(path):
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise GoalError(f"{path}: 无法读取：{error}") from error


def read_fields(path, names):
    try:
        # 字段只用物理 LF 分行。JSON 字符串中的 U+2028 等仍是值的一部分。
        rows = read_text(path).removesuffix("\n").split("\n")
        values = {}
        for row in rows:
            name, value = row.split(": ", 1)
            if name in values:
                raise ValueError(f"字段重复：{name}")
            values[name] = json.loads(value)
        if set(values) != set(names):
            raise ValueError("字段缺失或不认识")
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            raise ValueError("字段必须是非空字符串")
        return values
    except (OSError, ValueError) as error:
        raise GoalError(f"{path}: 格式错误：{error}") from error


def find_contract(root, session_id):
    # 定位只比较归属行的字节，不解码其他会话的目标、也不诊断它的坏文件。
    # 能认出本会话后，由 Contract 完整读取、报格式错误；重复归属仍在这里拒绝。
    session_row = f"session_id: {json.dumps(session_id, ensure_ascii=False)}".encode("utf-8")
    matches = []
    for path in sorted(root.glob("-*/*/xy-goal/goal.md")):
        try:
            with path.open("rb") as source:
                if any(row.rstrip(b"\r\n") == session_row for row in source):
                    matches.append(path.parent)
        except OSError:
            continue  # 归属不可读，不能把它当成本会话的契约来检查。
    if len(matches) > 1:
        raise GoalError(f"会话 {session_id} 对应多份契约：" + "、".join(map(str, matches)))
    return matches[0] if matches else None


def timer_prompt(contract):
    return f"本次契约：{contract}。按 xy-goal 的「定时自查」一节做"


def quote_files(directory):
    quotes = {}
    for path in directory.iterdir():
        if not path.is_file() or re.fullmatch(r"[1-9][0-9]*\.md", path.name) is None:
            raise GoalError(f"{path}: 原话文件名必须是无前导零的正整数.md")
        quotes[int(path.stem)] = read_text(path)
    return dict(sorted(quotes.items()))


def write_files(changes, removals):
    """先校验、写临时文件，再替换；I/O 失败也回滚已经替换的文件。"""
    staged = {}
    originals = {path: path.read_bytes() if path.exists() else None
                 for path in set(changes) | set(removals)}
    committed = []
    try:
        for path, content in changes.items():
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".goal-", delete=False) as output:
                staged[path] = Path(output.name)
                output.write(content.encode("utf-8"))
        for path, temporary in staged.items():
            os.replace(temporary, path)
            committed.append(path)
        for path in removals:
            path.unlink()
            committed.append(path)
    except BaseException:
        for path in reversed(committed):
            if originals[path] is None:
                path.unlink(missing_ok=True)
            else:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".goal-", delete=False) as output:
                    temporary = Path(output.name)
                    output.write(originals[path])
                try:
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def encode_links(links, dismissed):
    rows = list(links)
    # 挂接意味着要跟进；在唯一编码处作废不必跟进，读入仍可按挂接解释旧并存行。
    linked = {number for number, _ in links}
    rows.extend((number, "不必跟进 " + reason) for number, reason in dismissed.items()
                if number not in linked)
    rows.sort(key=lambda row: (row[0], int(row[1][1:]) if row[1].startswith("T") else 0))
    return "".join(f"{number} {landing}\n" for number, landing in rows)


def parse_links(text, quotes, tasks):
    """逐行检查，返回合格映射、不必跟进及不合格的原行与原因。"""
    links, dismissed, invalid = set(), {}, []
    for line in text.splitlines():
        try:
            match = re.fullmatch(r"([1-9][0-9]*) (T[1-9][0-9]*|不必跟进 .+)", line)
            if match is None:
                raise GoalError(f"关系格式错误：{line}")
            number, landing = int(match[1]), match[2]
            if number not in quotes:
                raise GoalError(f"原话 {number} 不存在")
            if landing.startswith("不必跟进 "):
                if number in dismissed or not landing[5:].strip():
                    raise GoalError("不必跟进原因为空或重复")
                dismissed[number] = landing[5:]
            else:
                if landing not in tasks:
                    raise GoalError(f"事项 {landing} 不存在")
                if (number, landing) in links:
                    raise GoalError(f"关系重复：{line}")
                links.add((number, landing))
        except GoalError as error:
            invalid.append((line, str(error)))
    return links, dismissed, invalid


class Contract:
    def __init__(self, path):
        self.path = path
        self.metadata = read_fields(path / "goal.md", GOAL_FIELDS)
        if re.fullmatch(r"[1-9][0-9]*", self.metadata["claude_pid"]) is None:
            raise GoalError(f"{path / 'goal.md'}: claude_pid 必须是正整数")
        self.quotes = quote_files(path / "quotes")
        self.tasks = self.read_tasks("tasks")
        self.done = self.read_tasks("done")
        overlap = self.tasks.keys() & self.done.keys()
        if overlap:
            raise GoalError(f"{path / 'done'}: 事项同时开放和关闭：{sorted(overlap)}")
        links_path = path / "links.md"
        self.links, self.dismissed, invalid = parse_links(
            read_text(links_path), self.quotes, self.tasks.keys() | self.done.keys())
        if invalid:
            raise GoalError(f"{links_path}: {invalid[0][1]}{RELINK_USAGE}")
        for task in self.tasks:
            # T1 的唯一特例是可无出处。
            if task != "T1" and not self.sources(task):
                raise GoalError(f"{path / 'tasks' / (task + '.md')}: 开放事项没有出处{RELINK_USAGE}")
        for task, values in self.done.items():
            if "cancel_quote" in values:
                number = int(values["cancel_quote"])
                if number not in self.quotes or (number, task) not in self.links:
                    raise GoalError(f"{path / 'done' / (task + '.md')}: 取消原话不存在或缺少映射")

    def read_tasks(self, directory):
        tasks = {}
        for path in (self.path / directory).iterdir():
            if not path.is_file() or re.fullmatch(r"T[1-9][0-9]*\.md", path.name) is None:
                raise GoalError(f"{path}: 事项文件名必须是 T 加正整数.md")
            fields = list(TASK_FIELDS.values())
            if directory == "done":
                text = read_text(path)
                fields.append("verdict" if "\nverdict: " in text else "cancel_quote")
            values = read_fields(path, fields)
            try:
                require_state(values["state"])
                if "cancel_quote" in values and re.fullmatch(r"[1-9][0-9]*", values["cancel_quote"]) is None:
                    raise GoalError("取消原话必须是正整数")
            except GoalError as error:
                raise GoalError(f"{path}: {error}") from error
            tasks[path.stem] = values
        return dict(sorted(tasks.items(), key=lambda item: int(item[0][1:])))

    def sources(self, task):
        return sorted(number for number, target in self.links if target == task)

    def pending(self):
        handled = {number for number, _ in self.links} | self.dismissed.keys()
        return sorted(self.quotes.keys() - handled)

    def require_sources(self, sources):
        for number in sources:
            if number not in self.quotes:
                raise GoalError(f"原话 {number} 不存在：{self.path / 'quotes'}")

    def require_task(self, task):
        if task not in self.tasks:
            raise GoalError(f"开放事项 {task} 不存在")

    def show(self):
        lines = [f"目标：{self.metadata['target']}"]
        warnings = []
        for task, values in self.tasks.items():
            # 保持清单每项四行；原值中的换行在此显示为转义，不改文件里的值。
            display = {key: value.translate({ord(char): json.dumps(char, ensure_ascii=True)[1:-1]
                                             for char in LINE_BREAKS})
                       for key, value in values.items()}
            item = (f"{task} {display['title']}\n  判据：{display['criteria']}\n"
                    f"  出处：原话 {'、'.join(map(str, self.sources(task)))}\n"
                    f"  状态：{display['state']}；指针：{display['pointer']}")
            lines.append(item)
            if len(item) > ITEM_LIMIT:
                warnings.append(f"提示：{task} 四行 {len(item)} 字，超过 {ITEM_LIMIT} 字，请压短")
        lines.append("还没处理的原话：" + ("、".join(map(str, self.pending())) or "无"))
        text = "\n".join(lines) + "\n"
        if len(text) > TOTAL_LIMIT:
            warnings.append(f"提示：清单全文 {len(text)} 字，超过 {TOTAL_LIMIT} 字，请压短")
        print(text + "".join(warning + "\n" for warning in warnings), end="")

    def change(self, options):
        command = options.command
        changes, removals = {}, []
        if command == "add":
            values = {name: getattr(options, name) for name in TASK_FIELDS.values()}
            for label, name in TASK_FIELDS.items():
                require_text(values[name], label)
            require_state(values["state"])
            self.require_sources(options.source)
            number = max([int(task[1:]) for task in self.tasks.keys() | self.done.keys()], default=0) + 1
            task = f"T{number}"
            self.links.update((number, task) for number in options.source)
            changes[self.path / "tasks" / f"{task}.md"] = encode_fields(values)
        elif command == "update":
            task = options.task
            self.require_task(task)
            updated_values = {
                name: getattr(options, name)
                for name in TASK_FIELDS.values()
                if getattr(options, name) is not None
            }
            if not updated_values and options.source is None:
                raise GoalError("至少改一项")
            labels = {name: label for label, name in TASK_FIELDS.items()}
            for name, value in updated_values.items():
                require_text(value, labels[name])
                if name == "state":
                    require_state(value)
            if "criteria" in updated_values and options.source is None:
                raise GoalError("改判据必须带 --source")
            if options.source is not None:
                self.require_sources(options.source)
                self.links.update((number, task) for number in options.source)
            if updated_values:
                self.tasks[task].update(updated_values)
                changes[self.path / "tasks" / f"{task}.md"] = encode_fields(self.tasks[task])
        elif command == "dismiss":
            self.require_sources([options.quote])
            require_text(options.reason, "原因")
            if any(char in options.reason for char in LINE_BREAKS):
                raise GoalError("原因必须写成一行")
            targets = sorted(target for number, target in self.links if number == options.quote)
            if targets:
                raise GoalError(f"原话 {options.quote} 已挂事项 " + "、".join(targets))
            self.dismissed[options.quote] = options.reason
        elif command == "reopen":
            task = options.task
            if task not in self.done:
                raise GoalError(f"已关闭事项 {task} 不存在")
            if not self.sources(task):
                raise GoalError(f"已关闭事项 {task} 没有出处，不能重开")
            # 只恢复四个开放字段；旧结论丢弃，取消依据映射仍保留在 links.md。
            values = {name: self.done[task][name] for name in TASK_FIELDS.values()}
            changes[self.path / "tasks" / f"{task}.md"] = encode_fields(values)
            removals.append(self.path / "done" / f"{task}.md")
        else:
            task = options.task
            self.require_task(task)
            if command == "accept":
                require_text(options.verdict, "验收结论")
                conclusion = {"verdict": options.verdict}
            else:
                self.require_sources([options.quote])
                conclusion = {"cancel_quote": str(options.quote)}
                self.links.add((options.quote, task))
            original = self.path / "tasks" / f"{task}.md"
            # 统一编码已解析字段与结论，不依赖被接受的原文件是否带末尾 LF。
            changes[self.path / "done" / f"{task}.md"] = encode_fields({**self.tasks[task], **conclusion})
            removals.append(original)
        changes[self.path / "links.md"] = encode_links(self.links, self.dismissed)
        write_files(changes, removals)
        if command == "add":
            print(task)


def parser():
    result = ArgumentParser(description=__doc__)
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
    cancelling.add_argument("--quote", type=int, required=True)
    reopening = commands.add_parser("reopen")
    reopening.add_argument("task")
    return result


@contextmanager
def root_lock(root):
    # 锁目录 fd，不新增持久锁文件。所有命令共用这把锁，使领 inbox、换会话和
    # hook 的编号/映射写入不会交错；原话创建仍用 x（O_EXCL），不覆盖旧原话。
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        # 根必须已存在；等锁期间路径可能被删除或替换，不能向失效的根写入。
        if not os.path.samefile(root, descriptor):
            raise GoalError(f"根目录已被替换：{root}")
        yield
    finally:
        os.close(descriptor)


def automatic_reason(text, contract):
    # hook 没有可靠的来源字段。只认完整正文；人手完整粘贴的通知也会命中，
    # 所以保留原文，关闭前由新开的子代理复核所有「不必跟进」。
    if text == timer_prompt(contract):
        return "自动：本契约定时提示"
    if (text.startswith("<task-notification>") and text.endswith("</task-notification>")
            and all(re.search(f"<{tag}>.*?</{tag}>", text, re.DOTALL)
                    for tag in ("task-id", "status", "summary"))):
        return "自动：完整后台任务通知"
    return None


def require_project_name(name):
    require_name(name, "项目目录名")
    if not name.startswith("-"):
        raise GoalError("项目目录名必须以 - 开头")
    return name


def require_project_directory(root, project):
    # 清理器不沿项目软链走；写入也只接受根下的真实项目目录。
    if project.parent != root or not stat.S_ISDIR(project.lstat().st_mode):
        raise GoalError(f"项目目录必须是根目录下的真实目录，不能是软链接：{project}")


def session_project(session_id):
    projects = Path.home() / ".claude" / "projects"
    records = [path for path in projects.glob(f"*/{session_id}.jsonl") if path.is_file()]
    if len(records) != 1:
        raise GoalError(f"会话记录必须恰好一份：{projects}/*/{session_id}.jsonl，找到 {len(records)} 份")
    return require_project_name(records[0].parent.name)


def read_inbox(directory):
    return quote_files(directory) if directory.exists() else {}


def incoming_quotes(inbox, contract, last_number):
    quotes = read_inbox(inbox)
    imported, reasons = {}, {}
    for number, text in enumerate(quotes.values(), start=last_number + 1):
        imported[number] = text
        reason = automatic_reason(text, contract)
        if reason is not None:
            reasons[number] = reason
    return imported, reasons


def remove_temporary_path(path):
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as error:
        print(f"警告：临时输入或目录清理失败，残留在 {path}：{error}", file=sys.stderr)


@contextmanager
def consume_inputs(paths):
    """输入先移出可领取的位置；主体失败放回，成功后删除失败只警告。"""
    moved = []
    try:
        for source in paths:
            # 同一父目录内改名，不改目录的 ..；只读 inbox 也能先移出领取位置。
            destination = Path(tempfile.mkdtemp(dir=source.parent, prefix=".goal-inputs-"))
            destination.rmdir()
            source.rename(destination)
            moved.append((source, destination))
        yield
    except BaseException:
        # 若回滚本身失败，不删除临时位置的原件，让错误路径指出残留位置。
        for source, destination in reversed(moved):
            destination.rename(source)
        raise
    else:
        for _, destination in moved:
            remove_temporary_path(destination)


def publish_contract(root, destination, metadata, quotes, links, input_paths):
    """先在临时目录写完并重新读取校验，再发布。失败时不留下新契约。"""
    item = destination.parent
    project = item.parent
    project.mkdir(exist_ok=True)
    require_project_directory(root, project)
    if item.exists():
        raise GoalError(f"事项目录已存在：{item}")
    temporary = Path(tempfile.mkdtemp(dir=root, prefix=".goal-"))
    try:
        staging = temporary / "item" / "xy-goal"
        staging.mkdir(parents=True)
        for name in ("quotes", "tasks", "done"):
            (staging / name).mkdir()
        for number, text in quotes.items():
            (staging / "quotes" / f"{number}.md").write_bytes(text.encode("utf-8"))
        (staging / "goal.md").write_text(encode_fields(metadata), encoding="utf-8")
        (staging / "links.md").write_text(links, encoding="utf-8")
        Contract(staging)
        # 最后的 rename 是主体提交点；此前的输入移动均可恢复，此后只做尽力清理。
        with consume_inputs(input_paths):
            staging.parent.rename(item)
    finally:
        remove_temporary_path(temporary)


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
    path = find_contract(root, session_id)
    validation_error = None
    if path is not None:
        require_project_directory(root, path.parents[1])
        try:
            contract = Contract(path)
        except (GoalError, OSError) as error:
            validation_error = error
        directory = path / "quotes"
        quotes = quote_files(directory)
        # 与 parse_links 一样按 splitlines 分行，跳过所有行首编号，避免悬空映射认领新原话。
        reserved = {int(match[0]) for line in read_text(path / "links.md").splitlines()
                    if (match := re.match(r"[0-9]+", line)) is not None}
        number = max(quotes.keys() | reserved, default=0) + 1
    else:
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


def require_pid(claude_pid):
    if claude_pid <= 0:
        raise GoalError("本会话的 claude 进程号必须是正整数；" + PID_USAGE)
    # 只核数字父链，不按 comm/argv[0] 认程序；抄别人的活 PID 也不能取得归属。
    current = os.getpid()
    try:
        while current > 1:
            fields = (Path("/proc") / str(current) / "stat").read_bytes().rsplit(b")", 1)[1].split()
            current = int(fields[1])
            if current == claude_pid:
                return
    except (OSError, ValueError, IndexError) as error:
        raise GoalError(f"无法核对调用进程的祖先：{error}；{PID_USAGE}") from error
    raise GoalError(f"进程 {claude_pid} 不是当前调用进程的祖先；{PID_USAGE}")


def relink(path, task, sources):
    """唯一先写后整体校验的入口：多个无出处事项须逐项修，进度不能因剩余错误回滚。

    不并入 update，保持正常命令先校验、拒绝时不写入的含义。
    """
    if (task is None) != (sources is None):
        raise GoalError("事项与 --source 必须同时给或同时不给")
    try:
        Contract(path)
    except GoalError:
        pass
    else:
        raise GoalError("契约完好，挂出处用 update")
    links_path = path / "links.md"
    text = read_text(links_path)
    quotes = quote_files(path / "quotes")
    # 修复不能依赖事项正文有效；这里只按开放、关闭目录下的文件名判断归属。
    tasks = {file.stem for file in (path / "tasks").glob("*.md") if file.is_file()}
    done = {file.stem for file in (path / "done").glob("*.md") if file.is_file()}
    if task is not None:
        if re.fullmatch(r"T[1-9][0-9]*", task) is None or task not in tasks:
            raise GoalError(f"开放事项 {task} 不存在")
        for number in sources:
            if number not in quotes:
                raise GoalError(f"原话 {number} 不存在：{path / 'quotes'}")
    links, dismissed, invalid = parse_links(text, quotes, tasks | done)
    for line, reason in invalid:
        print(f"丢弃：{line}；{reason}")
    if task is not None:
        links.update((number, task) for number in sources)
    write_files({links_path: encode_links(links, dismissed)}, [])
    Contract(path)
    print(f"修复完成：{path}")
    return 0


def execute(options, *, root, session_id, claude_pid, now):
    path = find_contract(root, session_id)
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
        else:
            path = Path(options.directory).expanduser().resolve()
            parts = path.relative_to(root).parts if path.is_relative_to(root) else ()
            if len(parts) != 3 or not parts[0].startswith("-") or parts[2] != "xy-goal":
                raise GoalError(f"契约目录必须是 <根>/<以 - 开头的项目目录>/<一件事>/xy-goal：{path}")
            require_project_directory(root, path.parents[1])
            contract = Contract(path)
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
    if path is None:
        raise GoalError("本会话没有契约")
    if options.command == "relink":
        return relink(path, options.task, options.source)
    contract = Contract(path)
    if options.command == "show":
        contract.show()
    elif options.command == "check":
        if contract.pending():
            for number in contract.pending():
                print(f"{contract.path / 'quotes' / f'{number}.md'}: 原话 {number} 还没处理", file=sys.stderr)
            return 1
        print(f"通过：{contract.path}")
    elif options.command == "close":
        if contract.tasks:
            raise GoalError("仍有开放事项：" + "、".join(contract.tasks))
        if contract.pending():
            raise GoalError("仍有还没处理的原话：" + "、".join(map(str, contract.pending())))
        # 子代理整体复核、CronDelete 和汇报由 agent 按 SKILL.md 完成。
        (contract.path / "goal.md").unlink()
    else:
        contract.change(options)
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
        # 所以 hook 的任何失败都输出 JSON、退出 0，不写 stderr；参数解析失败时 argparse
        # 不给出子命令，下面按它的规则自己扫一遍参数，认出是不是 hook。
        if options is not None:
            command = options.command
        else:
            command = None
            skip_root_value = False
            for argument in arguments:
                if skip_root_value and not argument.startswith("-"):
                    skip_root_value = False
                    continue
                # argparse 也接受 --root 的缩写；以 - 开头的 token 继续作为选项扫描。
                skip_root_value = argument.startswith("--r") and "--root".startswith(argument)
                if not argument.startswith("-"):
                    command = argument
                    break
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
