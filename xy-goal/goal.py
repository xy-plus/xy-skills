#!/usr/bin/env python3
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
import sys
import tempfile


# 用户要求的，保留：show 只提示压短，不阻止写入或 check。
ITEM_LIMIT = 300
TOTAL_LIMIT = 5000
TASK_FIELDS = {"标题": "title", "判据": "criteria", "状态": "state", "指针": "pointer"}
GOAL_FIELDS = ("target", "session_id", "claude_pid")
LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\u0085\u2028\u2029"
PID_USAGE = ('请在 Claude Code 的 Bash 工具中运行，在 open/adopt/migrate 命令后传入 '
             '--claude-pid "$PPID"（由该 Bash 展开）')
# 迁入的账本只保留原行，不能为旧记录补造判据或验收字段；读取 done/ 时仍需认它。
LEDGER_ACCEPTED = re.compile(r"^-\s+(T[1-9][0-9]*)\s+.+（.+；验收\s+[^）]+）$")
LEDGER_CANCELLED = re.compile(r"^-\s+\[~\]\s+(T[1-9][0-9]*)\s+.+（原话\s+([1-9][0-9]*)：「[^」]+」）$")


class GoalError(ValueError):
    """可向调用者直接说明的契约或用法错误。"""


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
    for path in sorted(root.glob("*/goal.md")):
        if path.parent.name in ("archive", "inbox"):
            continue
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
    return f"本次契约：{contract}。按 `~/.claude/skills/xy-goal/SKILL.md`「定时自查」一节做"


def quote_files(directory):
    paths = list(directory.iterdir())
    for path in paths:
        if not path.is_file() or re.fullmatch(r"[1-9][0-9]*\.md", path.name) is None:
            raise GoalError(f"{path}: 原话文件名必须是无前导零的正整数.md")
        read_text(path)
    return {int(path.stem): path for path in sorted(paths, key=lambda p: int(p.stem))}


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


def ledger_task_id(line):
    match = LEDGER_ACCEPTED.fullmatch(line) or LEDGER_CANCELLED.fullmatch(line)
    if match is None:
        raise GoalError("账本行必须是验收记录或带原话编号的取消记录")
    return match[1]


def source_numbers(text):
    numbers = set()
    for match in re.finditer(r"原话\s+([0-9]+(?:\s*、\s*[0-9]+)*)", text):
        numbers.update(int(part) for part in re.split(r"\s*、\s*", match[1]))
    return numbers


def encode_links(links, dismissed):
    rows = list(links)
    rows.extend((number, "不必跟进 " + reason) for number, reason in dismissed.items())
    rows.sort(key=lambda row: (row[0], 0 if row[1].startswith("T") else 1,
                               int(row[1][1:]) if row[1].startswith("T") else row[1]))
    return "".join(f"{number} {landing}\n" for number, landing in rows)


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
        self.links = set()
        self.dismissed = {}
        links_path = path / "links.md"
        for line in read_text(links_path).splitlines():
            match = re.fullmatch(r"([1-9][0-9]*) (T[1-9][0-9]*|不必跟进 .+)", line)
            if match is None:
                raise GoalError(f"{links_path}: 关系格式错误：{line}")
            number, landing = int(match[1]), match[2]
            if number not in self.quotes:
                raise GoalError(f"{links_path}: 原话 {number} 不存在")
            if landing.startswith("不必跟进 "):
                if number in self.dismissed or not landing[5:].strip():
                    raise GoalError(f"{links_path}: 不必跟进原因为空或重复")
                self.dismissed[number] = landing[5:]
            else:
                if landing not in self.tasks and landing not in self.done:
                    raise GoalError(f"{links_path}: 事项 {landing} 不存在")
                if (number, landing) in self.links:
                    raise GoalError(f"{links_path}: 关系重复：{line}")
                self.links.add((number, landing))
        for task in self.tasks:
            if not self.sources(task):
                raise GoalError(f"{path / 'tasks' / (task + '.md')}: 开放事项没有出处")
        for task, values in self.done.items():
            if "legacy_line" in values:
                if bool(self.sources(task)) == values["without_source"]:
                    raise GoalError(f"{path / 'done' / (task + '.md')}: 旧记录无出处标记与映射不符")
                for number in source_numbers(values["legacy_line"]):
                    if (number, task) not in self.links:
                        raise GoalError(f"{path / 'done' / (task + '.md')}: 账本正文原话缺少映射")
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
                if text.startswith("- "):
                    rows = text.splitlines()
                    try:
                        if ledger_task_id(rows[0]) != path.stem:
                            raise GoalError("账本 T 号与文件名不符")
                        if len(rows) > 2 or (len(rows) == 2 and rows[1] != "旧记录无出处"):
                            raise GoalError("旧账本只能保留原行和无出处标记")
                    except GoalError as error:
                        raise GoalError(f"{path}: {error}") from error
                    tasks[path.stem] = {"legacy_line": rows[0], "without_source": len(rows) == 2}
                    continue
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

    def links_text(self):
        return encode_links(self.links, self.dismissed)

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
            if options.field is None and options.value is not None:
                raise GoalError("--value 必须同时带 --field")
            if options.field is None and options.source is None:
                raise GoalError("必须指定 --field 和 --value，或 --source")
            if options.field is not None:
                if options.field not in TASK_FIELDS:
                    raise GoalError(f"不认识的字段：{options.field}")
                if options.value is None:
                    raise GoalError("--field 必须同时带 --value")
                require_text(options.value, options.field)
                if options.field == "状态":
                    require_state(options.value)
                if options.field == "判据" and options.source is None:
                    raise GoalError("改判据必须带 --source")
            if options.source is not None:
                self.require_sources(options.source)
                self.links.update((number, task) for number in options.source)
            if options.field is not None:
                self.tasks[task][TASK_FIELDS[options.field]] = options.value
                changes[self.path / "tasks" / f"{task}.md"] = encode_fields(self.tasks[task])
        elif command == "dismiss":
            self.require_sources([options.quote])
            require_text(options.reason, "原因")
            if any(char in options.reason for char in LINE_BREAKS):
                raise GoalError("原因必须写成一行")
            self.dismissed[options.quote] = options.reason
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
        changes[self.path / "links.md"] = self.links_text()
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
    for name in ("hook-prompt", "hook-answer"):
        commands.add_parser(name)
    adopting = commands.add_parser("adopt")
    adopting.add_argument("directory")
    migrating = commands.add_parser("migrate")
    migrating.add_argument("checklist")
    for command in (opening, adopting, migrating):
        command.add_argument("--claude-pid", type=int, required=True,
                             help='在 Claude Code 的 Bash 中用 "$PPID" 显式传入本会话进程号')
    adding = commands.add_parser("add")
    for name in TASK_FIELDS.values():
        adding.add_argument(f"--{name}", required=True)
    adding.add_argument("--source", type=int, nargs="+", required=True)
    updating = commands.add_parser("update")
    updating.add_argument("task")
    updating.add_argument("--field")
    updating.add_argument("--value")
    updating.add_argument("--source", type=int, nargs="+")
    dismissing = commands.add_parser("dismiss")
    dismissing.add_argument("quote", type=int)
    dismissing.add_argument("--reason", required=True)
    accepting = commands.add_parser("accept")
    accepting.add_argument("task")
    accepting.add_argument("--verdict", required=True)
    cancelling = commands.add_parser("cancel")
    cancelling.add_argument("task")
    cancelling.add_argument("--quote", type=int, required=True)
    return result


@contextmanager
def root_lock(root):
    # 锁目录 fd，不新增持久锁文件。所有命令共用这把锁，使领 inbox、换会话和
    # hook 的编号/映射写入不会交错；原话创建仍用 x（O_EXCL），不覆盖旧原话。
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
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


def read_inbox(root, session_id):
    directory = root / "inbox" / session_id
    if not directory.exists():
        return {}
    return {number: read_text(path) for number, path in quote_files(directory).items()}


def incoming_quotes(root, session_id, contract, last_number):
    quotes = read_inbox(root, session_id)
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


def publish_contract(root, destination, metadata, quotes, tasks, done, links, expected_counts, input_paths):
    """先在临时目录写完并重新读取校验，再发布。失败时不留下新契约。"""
    if destination.exists():
        raise GoalError(f"契约目录已存在：{destination}")
    temporary = Path(tempfile.mkdtemp(dir=root, prefix=".goal-"))
    try:
        staging = temporary / "contract"
        staging.mkdir()
        for name, files in (("quotes", quotes), ("tasks", tasks), ("done", done)):
            directory = staging / name
            directory.mkdir()
            for number, text in files.items():
                (directory / f"{number}.md").write_bytes(text.encode("utf-8"))
        (staging / "goal.md").write_text(encode_fields(metadata), encoding="utf-8")
        (staging / "links.md").write_text(links, encoding="utf-8")
        saved = Contract(staging)
        actual_counts = (len(saved.quotes), len(saved.tasks), len(saved.done))
        if actual_counts != expected_counts:
            raise GoalError(f"迁移条数校验失败：原话、开放事项、账本应为 {expected_counts}，实际 {actual_counts}")
        # 最后的 rename 是主体提交点；此前的输入移动均可恢复，此后只做尽力清理。
        with consume_inputs(input_paths):
            staging.rename(destination)
    finally:
        remove_temporary_path(temporary)


def parse_legacy_quotes(path):
    text = read_text(path)
    matches = list(re.finditer(r"^（原话\s+([0-9]+)\s*→\s*(.*?)）", text, re.MULTILINE))
    prefix = text[:matches[0].start()] if matches else text
    if prefix.strip():
        raise GoalError(f"{path}: 原话文件必须从顶格标注开始")
    if len(list(re.finditer(r"^（原话", text, re.MULTILINE))) != len(matches):
        raise GoalError(f"{path}: 原话顶格标注格式错误")
    quotes, links, dismissed = {}, set(), {}
    for index, match in enumerate(matches):
        number = int(match[1])
        if number <= 0 or number in quotes or (quotes and number <= max(quotes)):
            raise GoalError(f"{path}: 原话编号重复或不按顺序")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        # 仅去掉顶格标注，连同原消息的空行、缩进和分隔空行一起保留，绝不猜正文边界。
        quotes[number] = text[match.end():end]
        reasons = []
        for segment in match[2].split("；"):
            segment = segment.strip()
            if segment.startswith("无：") or segment.startswith("规矩："):
                label, reason = segment.split("：", 1)
                require_text(reason, f"{path}: 旧落点原因")
                reasons.append("旧规矩落点" if label == "规矩" else reason)
            else:
                targets = [part.strip() for part in segment.split("、")]
                if any(re.fullmatch(r"T[1-9][0-9]*", target) is None for target in targets):
                    raise GoalError(f"{path}: 原话 {number} 落点格式错误")
                links.update((number, target) for target in targets)
        if reasons:
            dismissed[number] = "；".join(reasons)
    return quotes, links, dismissed, len(matches)


def parse_legacy_checklist(path, claude_pid):
    lines = read_text(path).splitlines()
    if not lines or not lines[0].startswith("# ") or lines.count("## 清单") != 1:
        raise GoalError(f"{path}: 旧清单缺目标或「## 清单」")
    title = lines[0][2:]
    require_text(title, f"{path}: 目标")
    start = lines.index("## 清单")
    header = {}
    for row in lines[1:start]:
        if not row.strip():
            continue
        # 已知旧清单还会指向 archive 的笔记；只丢弃这行元数据，不读取或复制笔记。
        if row.startswith("笔记："):
            continue
        match = re.fullmatch(r"(session|cron_job_id)\s*:\s*(.+)", row)
        if match is None or match[1] in header:
            raise GoalError(f"{path}: 旧清单头格式错误")
        header[match[1]] = match[2]
    if "session" not in header or re.fullmatch(r"[1-9][0-9]*", header["session"]) is None:
        raise GoalError(f"{path}: session 必须是 claude 进程号")
    if int(header["session"]) != claude_pid:
        raise GoalError(f"{path}: 旧清单不属于本会话，session 与 claude 进程号不同")
    if "cron_job_id" not in header:
        raise GoalError(f"{path}: 缺少 cron_job_id")
    tasks, sources = {}, {}
    index = start + 1
    count = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        match = re.fullmatch(r"- \[([ x])\] (T[1-9][0-9]*) (.+)", lines[index])
        if match is None:
            raise GoalError(f"{path}:{index + 1}: 旧事项标题格式错误")
        status, task, task_title = match.groups()
        if task in tasks:
            raise GoalError(f"{path}: 重复事项 {task}")
        count += 1
        index += 1
        if index >= len(lines) or not lines[index].startswith("  - 判据："):
            raise GoalError(f"{path}:{index + 1}: 缺少判据")
        criteria = [lines[index][len("  - 判据："):]]
        index += 1
        while index < len(lines) and not lines[index].startswith("  - 出处："):
            if not lines[index].strip() or lines[index].startswith(("- ", "  - ", "## ")):
                raise GoalError(f"{path}:{index + 1}: 缺少出处")
            criteria.append(lines[index])
            index += 1
        if index >= len(lines) or not lines[index].startswith("  - 出处："):
            raise GoalError(f"{path}: 缺少出处")
        sources[task] = source_numbers(lines[index])
        index += 1
        if index >= len(lines) or not lines[index].startswith("  - 产物："):
            raise GoalError(f"{path}:{index + 1}: 缺少产物")
        artifact = lines[index][len("  - 产物："):]
        index += 1
        segments = re.split(r"[；;]", artifact, maxsplit=2)
        if len(segments) < 2 or re.fullmatch(r"下次核：\s*[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}", segments[1].strip()) is None:
            raise GoalError(f"{path}: 旧产物第二段必须是下次核")
        head = segments[0]
        pointer = segments[2] if len(segments) == 3 else ""
        state = "在办"
        if status == "x" and head.startswith("待验收："):
            pointer = head[len("待验收："):] + ("；" + pointer if pointer else "")
        elif status == " " and head.startswith("等：用户："):
            state = "等用户：" + head[len("等：用户："):]
        elif status != " " or not (head.startswith("在跑：") or re.fullmatch(r"等：T[0-9]+(?:、T[0-9]+)*", head)):
            raise GoalError(f"{path}: 旧产物状态格式错误")
        values = {"title": task_title, "criteria": "\n".join(criteria), "state": state,
                  "pointer": pointer if pointer.strip() else "无"}
        for label, name in TASK_FIELDS.items():
            require_text(values[name], f"{path}: {label}")
        require_state(state)
        tasks[task] = encode_fields(values)
    return title, tasks, sources, count


def migrate_contract(root, old, session_id, claude_pid):
    """临时入口：各会话自己迁完后删除本入口、旧 check.py 及旧测试。"""
    try:
        if old.parent != root or old.suffix != ".md" or old.name.endswith((".quotes.md", ".done.md")):
            raise GoalError("旧清单必须是根目录内的清单 .md 文件")
        title, tasks, sources, task_count = parse_legacy_checklist(old, claude_pid)
        quotes_path, ledger_path = old.with_suffix(".quotes.md"), old.with_suffix(".done.md")
        quotes, links, dismissed, quote_count = parse_legacy_quotes(quotes_path)
        ledger = read_text(ledger_path).splitlines()
        done = {}
        for line in ledger:
            task = ledger_task_id(line)
            if task in done or task in tasks:
                raise GoalError(f"{ledger_path}: 重复事项 {task}")
            links.update((number, task) for number in source_numbers(line))
            has_source = any(target == task for _, target in links)
            done[task] = line + ("\n" if has_source else "\n旧记录无出处\n")
        for task, numbers in sources.items():
            from_annotations = {number for number, target in links if target == task}
            if not from_annotations or from_annotations != numbers:
                raise GoalError(f"开放事项 {task} 的出处与旧原话标注不符或没有出处")
        destination = old.with_suffix("")
        imported, reasons = incoming_quotes(root, session_id, destination, max(quotes, default=0))
        quotes.update(imported)
        dismissed.update(reasons)
        metadata = {"target": title, "session_id": session_id, "claude_pid": str(claude_pid)}
        inbox = root / "inbox" / session_id
        inputs = [old, quotes_path, ledger_path] + ([inbox] if inbox.exists() else [])
        publish_contract(root, destination, metadata, quotes, tasks, done, encode_links(links, dismissed),
                         (quote_count + len(imported), task_count, len(ledger)), inputs)
        print(timer_prompt(destination))
    except (GoalError, OSError) as error:
        raise GoalError(f"迁移 {old} 失败：{error}") from error


def cleanup_old_files(root, now):
    cutoff = now.timestamp() - 30 * 86400
    for name in ("archive", "inbox"):
        directory = root / name
        if directory.is_symlink():
            if directory.lstat().st_mtime < cutoff:
                directory.unlink()
            continue
        if not directory.exists():
            continue
        # 只看每个文件自己的 mtime；目录不按年龄整棵删除，也不沿软链走出根目录。
        for current, directories, files in os.walk(directory, topdown=False, followlinks=False):
            current = Path(current)
            for child in files + [child for child in directories if (current / child).is_symlink()]:
                path = current / child
                if path.lstat().st_mtime < cutoff:
                    path.unlink()
            if not any(current.iterdir()):
                current.rmdir()


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
    return session_id, text


def record_hook(root, session_id, text):
    path = find_contract(root, session_id)
    if path is not None:
        contract = Contract(path)
        directory = path / "quotes"
        number = max(contract.quotes, default=0) + 1
    else:
        contract = None
        quotes = read_inbox(root, session_id)
        directory = root / "inbox" / session_id
        directory.mkdir(parents=True, exist_ok=True)
        number = max(quotes, default=0) + 1
    quote = directory / f"{number}.md"
    output = quote.open("x", encoding="utf-8", newline="")
    try:
        with output:
            output.write(text)
        if contract is not None:
            reason = automatic_reason(text, path)
            if reason is not None:
                contract.dismissed[number] = reason
                write_files({contract.path / "links.md": contract.links_text()}, [])
    except BaseException:
        quote.unlink()
        raise


def require_pid(claude_pid):
    if not isinstance(claude_pid, int) or isinstance(claude_pid, bool) or claude_pid <= 0:
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


def execute(options, *, root, session_id, claude_pid, now):
    path = find_contract(root, session_id)
    if options.command in ("open", "adopt", "migrate"):
        require_pid(claude_pid)
        if path is not None and not (options.command == "adopt"
                and Path(options.directory).expanduser().resolve() == path):
            raise GoalError(f"本会话已有契约：{path}")
        if options.command == "migrate":
            migrate_contract(root, Path(options.checklist).expanduser().resolve(), session_id, claude_pid)
            return 0
        if options.command == "open":
            require_name(options.slug, "slug")
            require_text(options.target, "目标")
            path = root / f"{now:%Y%m%d-%H%M%S}-{options.slug}"
            quotes, reasons = incoming_quotes(root, session_id, path, 0)
            metadata = {"target": options.target, "session_id": session_id, "claude_pid": str(claude_pid)}
            links = "".join(f"{number} 不必跟进 {reason}\n" for number, reason in reasons.items())
            inbox = root / "inbox" / session_id
            publish_contract(root, path, metadata, quotes, {}, {}, links, (len(quotes), 0, 0),
                             [inbox] if inbox.exists() else [])
            try:
                cleanup_old_files(root, now)
            except OSError as error:
                print(f"警告：契约已生效，archive/inbox 清理失败：{error}", file=sys.stderr)
        else:
            path = Path(options.directory).expanduser().resolve()
            if path.parent != root or path.name in ("archive", "inbox"):
                raise GoalError(f"契约目录必须在根目录内：{path}")
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
                quotes, reasons = incoming_quotes(root, session_id, path, max(contract.quotes, default=0))
                contract.metadata.update(session_id=session_id, claude_pid=str(claude_pid))
                contract.dismissed.update(reasons)
                changes = {path / "quotes" / f"{number}.md": text for number, text in quotes.items()}
                changes[path / "goal.md"] = encode_fields(contract.metadata)
                changes[path / "links.md"] = contract.links_text()
                inbox = root / "inbox" / session_id
                with consume_inputs([inbox] if inbox.exists() else []):
                    write_files(changes, [])
        print(timer_prompt(path))
        return 0
    if path is None:
        raise GoalError("本会话没有契约")
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
        shutil.rmtree(contract.path)
    else:
        contract.change(options)
    return 0


def main(arguments, *, session_id, now):
    """根目录与进程号都从命令参数取；会话和时钟显式传入。"""
    try:
        options = parser().parse_args(arguments)
        # Claude Code 的 Bash 的 $PPID 是本会话 claude。由该 Bash 展开后传入，
        # 不依赖进程名、argv[0]，也不依赖未公开保证的 CLAUDE_PID 环境变量。
        claude_pid = options.claude_pid if options.command in ("open", "adopt", "migrate") else None
        incoming = None
        if options.command.startswith("hook-"):
            incoming = hook_input(options.command)
            if incoming is None:
                return 0
        else:
            require_name(session_id, "CLAUDE_CODE_SESSION_ID")
        root = Path(options.root).expanduser().resolve()
        with root_lock(root):
            if incoming is not None:
                record_hook(root, *incoming)
                return 0
            return execute(options, root=root, session_id=session_id, claude_pid=claude_pid, now=now)
    except (GoalError, OSError, UnicodeError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], session_id=os.environ.get("CLAUDE_CODE_SESSION_ID"), now=datetime.now()))
