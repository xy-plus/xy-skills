from contextlib import contextmanager
import fcntl
import glob
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile


ITEM_LIMIT, TOTAL_LIMIT = 300, 5000
TASK_FIELDS = {"标题": "title", "判据": "criteria", "状态": "state", "指针": "pointer"}
HOOK_COMMANDS = ("hook-prompt", "hook-answer")
RELINK_USAGE = "；用 relink [<事项> --source <编号>…] 修复，编号看 quotes/"
LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\u0085\u2028\u2029"
PID_USAGE = ('请在 Claude Code 的 Bash 工具中运行，在 open/adopt 命令后传入 '
             '--claude-pid "$PPID"（由该 Bash 展开）')


class GoalError(ValueError):
    """可向调用者直接说明的契约或用法错误。"""


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
    except UnicodeError as error:
        raise GoalError(f"{path}: 无法读取：{error}") from error


def parse_fields(path, names, content, ignored_fields):
    try:
        text = content.decode("utf-8")
        # 字段只用物理 LF 分行。JSON 字符串中的 U+2028 等仍是值的一部分。
        rows = text.removesuffix("\n").split("\n")
        values = {}
        for row in rows:
            name, value = row.split(": ", 1)
            if name in ignored_fields:
                continue
            if name in values:
                raise ValueError(f"字段重复：{name}")
            values[name] = json.loads(value)
        if set(values) != set(names):
            raise ValueError("字段缺失或不认识")
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            raise ValueError("字段必须是非空字符串")
        return values
    except UnicodeError as error:
        raise GoalError(f"{path}: 无法读取：{error}") from error
    except ValueError as error:
        raise GoalError(f"{path}: 格式错误：{error}") from error


def find_contract(root, session_id):
    # 定位只比较归属行的字节，不解码其他会话的目标、也不诊断它的坏文件。
    # 能认出本会话后，由 Contract 完整读取、报格式错误；重复归属仍在这里拒绝。
    session_row = f"session_id: {json.dumps(session_id, ensure_ascii=False)}".encode("utf-8")
    matches = []
    for path in sorted(root.glob("-*/*/xy-goal/goal.md")):
        try:
            contents = path.read_bytes()
            if any(row.rstrip(b"\r\n") == session_row for row in contents.split(b"\n")):
                matches.append((path.parent, contents))
        except OSError:
            continue  # 归属不可读，不能把它当成本会话的契约来检查。
    if len(matches) > 1:
        raise GoalError(f"会话 {session_id} 对应多份契约：" + "、".join(str(path) for path, _ in matches))
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
    # 挂接意味着要跟进；在唯一编码处作废不必跟进。
    linked = {number for number, _ in links}
    rows.extend((number, "不必跟进 " + reason) for number, reason in dismissed.items()
                if number not in linked)
    rows.sort(key=lambda row: (row[0], int(row[1][1:]) if row[1].startswith("T") else 0))
    return "".join(f"{number} {landing}\n" for number, landing in rows)


def parse_links(text, quotes, tasks):
    """逐行检查，返回合格映射、不必跟进及不合格的原行与原因。"""
    links, dismissed, dismissed_lines, invalid = set(), {}, {}, []
    for line_number, line in enumerate(text.splitlines()):
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
                dismissed_lines[number] = (line_number, line)
            else:
                if landing not in tasks:
                    raise GoalError(f"事项 {landing} 不存在")
                if (number, landing) in links:
                    raise GoalError(f"关系重复：{line}")
                links.add((number, landing))
        except GoalError as error:
            invalid.append((line_number, line, str(error)))
    linked_numbers = {number for number, _ in links}
    for number in linked_numbers & dismissed.keys():
        line_number, line = dismissed_lines[number]
        invalid.append((
            line_number, line,
            f"原话 {number} 已挂事项，不能同时不必跟进",
        ))
        del dismissed[number]
    invalid.sort(key=lambda item: item[0])
    return links, dismissed, [(line, reason) for _, line, reason in invalid]


class Contract:
    def __init__(self, path, goal_content):
        self.path = path
        self.metadata = parse_fields(
            path / "goal.md", ("target", "session_id", "claude_pid"), goal_content, (),
        )
        if re.fullmatch(r"[1-9][0-9]*", self.metadata["claude_pid"]) is None:
            raise GoalError(f"{path / 'goal.md'}: claude_pid 必须是正整数")
        self.quotes = quote_files(path / "quotes")
        self.tasks = self.read_tasks("tasks")
        self.done = self.read_tasks("done")
        overlap = self.tasks.keys() & self.done.keys()
        if overlap:
            raise GoalError(f"{path / 'done'}: 事项同时开放和关闭：{sorted(overlap)}")
        links_path = self.path / "links.md"
        self.links, self.dismissed, invalid = parse_links(
            read_text(links_path), self.quotes, self.tasks.keys() | self.done.keys())
        if invalid:
            raise GoalError(f"{links_path}: {invalid[0][1]}{RELINK_USAGE}")
        for number, task in self.links:
            if task not in self.tasks:
                # 关闭事项不再驱动工作；它的映射是历史，不能让整份契约因此读不出来。
                continue
            reason = automatic_reason(self.quotes[number], self.path)
            if reason is not None:
                # 机器输入挂上后，完好的契约里没有摘除映射的命令，因此只能在挂接时挡住。
                raise GoalError(f"原话 {number} 是机器输入（{reason}），不能作为出处{RELINK_USAGE}")
        linked_tasks = {task for _, task in self.links}
        for task in self.tasks:
            if task not in linked_tasks:
                raise GoalError(
                    f"{path / 'tasks' / (task + '.md')}: 开放事项 {task} 没有原话出处{RELINK_USAGE}"
                )
        for task, values in self.done.items():
            if "cancel_quote" in values:
                number = int(values["cancel_quote"])
                if number not in self.quotes or (number, task) not in self.links:
                    raise GoalError(
                        f"{path / 'done' / (task + '.md')}: 取消原话不存在或缺少映射")
            if "cancel_reason" in values:
                if "evidence" not in values:
                    raise GoalError(
                        f"{path / 'done' / (task + '.md')}: 取消原因只用于带证据的事项")

    def read_tasks(self, directory):
        tasks = {}
        for path in (self.path / directory).iterdir():
            if not path.is_file() or re.fullmatch(r"T[1-9][0-9]*\.md", path.name) is None:
                raise GoalError(f"{path}: 事项文件名必须是 T 加正整数.md")
            content = path.read_bytes()
            fields = list(TASK_FIELDS.values()) if directory == "tasks" else ["title", "criteria"]
            if b"\nevidence: " in content:
                fields.append("evidence")
            ignored_fields = ()
            if directory == "done":
                # 关闭后状态和指针已不再描述工作；旧档仍可读，但这两行不校验也不恢复。
                ignored_fields = ("state", "pointer")
                conclusions = [name for name in ("verdict", "cancel_quote", "cancel_reason")
                               if f"\n{name}: ".encode() in content]
                if len(conclusions) != 1:
                    raise GoalError(f"{path}: 格式错误：关闭事项必须且只能有一种结论")
                fields.append(conclusions[0])
            values = parse_fields(path, fields, content, ignored_fields)
            try:
                if directory == "tasks":
                    require_state(values["state"])
                if "evidence" in values and values["evidence"] == "无":
                    raise GoalError("作者发起证据不能写「无」")
                if "cancel_quote" in values and re.fullmatch(r"[1-9][0-9]*", values["cancel_quote"]) is None:
                    raise GoalError("取消原话必须是正整数")
            except GoalError as error:
                raise GoalError(f"{path}: {error}") from error
            tasks[path.stem] = values
        return dict(sorted(tasks.items(), key=lambda item: int(item[0][1:])))

    def pending(self):
        handled = {number for number, _ in self.links} | self.dismissed.keys()
        return sorted(self.quotes.keys() - handled)


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
    records = [path for path in projects.glob(f"*/{glob.escape(session_id)}.jsonl") if path.is_file()]
    if len(records) != 1:
        raise GoalError(f"会话记录必须恰好一份：{projects}/*/{session_id}.jsonl，找到 {len(records)} 份")
    return require_project_name(records[0].parent.name)


def actual_directory(path, error_message, *, create=False):
    if create:
        try:
            path.mkdir()
        except FileExistsError:
            pass
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode):
        raise GoalError(f"{error_message}：{path}")
    return True


def inbox_directory(root, project, session_id, *, create=False):
    """Return an actual inbox directory, rejecting symlinks at both levels."""
    require_name(session_id, "inbox 会话 id")
    if project.parent != root:
        raise GoalError(f"项目目录必须是根目录下的真实目录，不能是软链接：{project}")
    if not actual_directory(project, "项目目录必须是根目录下的真实目录，不能是软链接"):
        if create:
            raise GoalError(f"项目目录不存在，不能创建 inbox：{project}")
        return None

    inbox_root = project / "xy-goal-inbox"
    message = "项目 inbox 必须是实际目录，不能是软链接"
    if not actual_directory(inbox_root, message, create=create):
        return None
    inbox = inbox_root / session_id
    return inbox if actual_directory(inbox, message, create=create) else None


def checked_inbox_path(root, inbox):
    project = inbox.parent.parent
    if inbox.parent != project / "xy-goal-inbox":
        raise GoalError(f"项目 inbox 路径不符合约定：{inbox}")
    actual = inbox_directory(root, project, inbox.name)
    if actual is None:
        raise GoalError(f"项目 inbox 不存在：{inbox}")
    return actual


def read_inbox(root, project, session_id):
    directory = inbox_directory(root, project, session_id)
    return quote_files(directory) if directory is not None else {}


def incoming_quotes(root, project, session_id, contract, last_number):
    quotes = read_inbox(root, project, session_id)
    imported, reasons = {}, {}
    for number, text in enumerate(quotes.values(), start=last_number + 1):
        imported[number] = text
        reason = automatic_reason(text, contract)
        if reason is not None:
            reasons[number] = reason
    return imported, reasons


def merge_inbox(contract, session_id):
    """Import staged hook input before the next hook or explicit check."""
    root = contract.path.parents[2]
    project = contract.path.parents[1]
    require_project_directory(root, project)
    inbox = inbox_directory(root, project, session_id)
    if inbox is None:
        return contract
    quotes, reasons = incoming_quotes(
        root, project, session_id, contract.path, max(contract.quotes, default=0))
    if not quotes:
        return contract
    changes = {contract.path / "quotes" / f"{number}.md": text
               for number, text in quotes.items()}
    dismissed = dict(contract.dismissed)
    dismissed.update(reasons)
    changes[contract.path / "links.md"] = encode_links(contract.links, dismissed)
    with consume_inputs(root, [inbox]):
        write_files(changes, [])
    return Contract(contract.path, (contract.path / "goal.md").read_bytes())


def remove_temporary_path(path):
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as error:
        print(f"警告：临时输入或目录清理失败，残留在 {path}：{error}", file=sys.stderr)


@contextmanager
def consume_inputs(root, paths):
    """输入先移出可领取的位置；主体失败放回，成功后删除失败只警告。"""
    moved = []
    try:
        for source in paths:
            source = checked_inbox_path(root, source)
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


# Keep the values as named inputs; the spec rejects a one-off container for this call.
def publish_contract(root, destination, *, metadata, quotes, links, input_paths):  # noqa: PLR0913
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
        Contract(staging, (staging / "goal.md").read_bytes())
        # 最后的 rename 是主体提交点；此前的输入移动均可恢复，此后只做尽力清理。
        with consume_inputs(root, input_paths):
            staging.parent.rename(item)
    finally:
        remove_temporary_path(temporary)


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
