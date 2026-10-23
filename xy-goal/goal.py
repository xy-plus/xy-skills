#!/usr/bin/env python3
"""xy-goal 的唯一写入口；读取可直接用 Read/cat，映射只存 links.md。

Markdown 字段的值用 JSON 字符串编码，让换行和引号不会伪造新字段。
原话正文不经过这个编码器：quotes/N.md 保存收到的原文。
"""

import argparse
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


class GoalError(ValueError):
    """可向调用者直接说明的契约或用法错误。"""


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
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
        rows = read_text(path).splitlines()
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
    # 只用 goal.md 的会话字段定位；不加载或检查其他会话的事项、原话和映射。
    session_row = f"session_id: {json.dumps(session_id, ensure_ascii=False)}"
    matches = [path.parent for path in sorted(root.glob("*/goal.md"))
               if path.parent.name not in ("archive", "inbox")
               and session_row in read_text(path).splitlines()]
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
    """调用前完成全部语义校验；先写好临时文件，原子替换每个文件，不留下半个字段。"""
    staged = {}
    try:
        for path, content in changes.items():
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".goal-", delete=False) as output:
                staged[path] = Path(output.name)
                output.write(content.encode("utf-8"))
        for path, temporary in staged.items():
            os.replace(temporary, path)
        for path in removals:
            path.unlink()
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


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

    def links_text(self):
        rows = [(number, target) for number, target in self.links]
        rows.extend((number, "不必跟进 " + reason) for number, reason in self.dismissed.items())
        rows.sort(key=lambda row: (row[0], 0 if row[1].startswith("T") else 1,
                                   int(row[1][1:]) if row[1].startswith("T") else row[1]))
        return "".join(f"{number} {landing}\n" for number, landing in rows)

    def show(self):
        lines = [f"目标：{self.metadata['target']}"]
        warnings = []
        for task, values in self.tasks.items():
            # 保持清单每项四行；原值中的换行在此显示为转义，不改文件里的值。
            display = {key: value.replace("\r", "\\r").replace("\n", "\\n")
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
            if "\n" in options.reason or "\r" in options.reason:
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
            changes[self.path / "done" / f"{task}.md"] = read_text(original) + encode_fields(conclusion)
            removals.append(original)
        changes[self.path / "links.md"] = self.links_text()
        write_files(changes, removals)
        if command == "add":
            print(task)


def parser():
    result = ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    opening = commands.add_parser("open")
    opening.add_argument("slug")
    opening.add_argument("target")
    for name in ("show", "check", "close"):
        commands.add_parser(name)
    adding = commands.add_parser("add")
    for name in ("title", "criteria", "state", "pointer"):
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


def main(arguments, *, root, session_id, claude_pid, now):
    """依赖全部显式传入；测试不用真实家目录、会话或时钟。"""
    try:
        options = parser().parse_args(arguments)
        require_text(session_id, "CLAUDE_CODE_SESSION_ID")
        root = Path(root).resolve()
        contract = find_contract(root, session_id)
        if options.command == "open":
            if contract is not None:
                raise GoalError(f"本会话已有契约：{contract}")
            require_name(options.slug, "slug")
            require_text(options.target, "目标")
            contract = root / f"{now:%Y%m%d-%H%M%S}-{options.slug}"
            contract.mkdir(parents=True, exist_ok=False)
            for directory in ("quotes", "tasks", "done"):
                (contract / directory).mkdir()
            (contract / "goal.md").write_text(encode_fields({
                "target": options.target, "session_id": session_id,
                "claude_pid": str(claude_pid),
            }), encoding="utf-8")
            (contract / "links.md").write_text("", encoding="utf-8")
            print(timer_prompt(contract))
            return 0
        if contract is None:
            raise GoalError("本会话没有契约")
        contract = Contract(contract)
        if options.command == "show":
            contract.show()
        elif options.command == "check":
            if contract.pending():
                for number in contract.pending():
                    print(f"{contract.path / 'quotes' / f'{number}.md'}: 原话 {number} 还没处理",
                          file=sys.stderr)
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
    except (GoalError, OSError, UnicodeError) as error:
        print(error, file=sys.stderr)
        return 1
