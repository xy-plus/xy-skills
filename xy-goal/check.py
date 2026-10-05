#!/usr/bin/env python3
"""检查 xy-goal 契约的三个文件：清单的条目与产物栏、原话文件的标注与编号、账本的记录，
以及它们之间的交叉引用。报错格式 `<文件名>:<行号>: <说明>`，退出 1；通过退出 0；用法错退出 2。
每个函数的 docstring 写明它守 SKILL.md 哪一句。"""

from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import re
import sys


# 超过半天不核一次方向，就等于放着不管。
MAX_NEXT_CHECK_HOURS = 12

TASK_RE = re.compile(r"^\s*-\s*\[([^\]]*)\]\s*(\S+)(?:\s+.*)?$")
TASK_ID_RE = re.compile(r"T\d+")
WAIT_TARGETS_RE = re.compile(r"^T\d+(?:\s*、\s*T\d+)*$")  # 多个目标只认「、」
QUOTE_RE = re.compile(r"^（原话\s+(\d+)\s*→\s*(.*?)）")  # 标注必须顶格
SOURCE_RE = re.compile(r"原话\s+(\d+(?:\s*、\s*\d+)*)")
CRON_RE = re.compile(r"^\s*cron_job_id\s*:")
SESSION_RE = re.compile(r"^\s*session\s*:\s*(.*?)\s*$")
# 名字里不许有括号和分隔符，所以两个推进者匹配不上。
RUNNING_ENTRY_RE = re.compile(r"^[^（）、；;]+（pid ([1-9]\d*)）$")
NEXT_CHECK_RE = re.compile(r"^下次核：\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})$")

LEDGER_ACCEPTED_RE = re.compile(r"^-\s+(T\d+)\s+.+（.+；验收\s+[^）]+）$")
LEDGER_CANCELLED_RE = re.compile(r"^-\s+\[~\]\s+(T\d+)\s+.+（原话\s+(\d+)：「[^」]+」）$")


@dataclass
class Problem:
    path: Path
    line: int
    message: str


@dataclass
class Task:
    task_id: str
    status: str
    source_line: int
    artifact_line: int
    source_ids: set = field(default_factory=set)
    wait_targets: list = field(default_factory=list)
    running_name: str = ""
    next_check_at: datetime | None = None


@dataclass
class Contract:
    path: Path
    session: str
    tasks: dict = field(default_factory=dict)


def _line_number(index):
    """报错里的行号从 1 起，列表索引从 0 起。"""
    return index + 1


def _source_numbers(value):
    """SKILL.md「契约」：出处里的原话编号，多个用「、」。只提取「原话 N、M」里的编号；
    逗号、「～」写法由 _parse_contract 报错，不在这里猜。"""
    numbers = set()
    for match in SOURCE_RE.finditer(value):
        numbers.update(int(part) for part in re.split(r"\s*、\s*", match.group(1)))
    return numbers


def _find_contract_files(directory):
    """SKILL.md「契约」：一份契约三个同名文件；清单是目录顶层不以 .quotes.md、.done.md 结尾的 *.md。"""
    return sorted(
        (path for path in directory.glob("*.md")
         if not path.name.endswith((".quotes.md", ".done.md"))),
        key=lambda path: path.name,
    )


def _quotes_path(contract_path):
    """原话文件：和清单同名，后缀 .quotes.md。"""
    return contract_path.with_name(contract_path.stem + ".quotes.md")


def _ledger_path(contract_path):
    """账本：和清单同名，后缀 .done.md。"""
    return contract_path.with_name(contract_path.stem + ".done.md")


def _read_lines(path, problems):
    """按 UTF-8 读成行；读不了就记一条问题并返回 None，调用方跳过这个文件。"""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        problems.append(Problem(path, 1, f"无法读取文件：{error}"))
        return None


def _parse_next_check(path, line_number, segments, task, problems):
    """SKILL.md「契约」：产物栏第二段一律是「下次核：YYYY-MM-DD HH:MM」，只有一个；每个开放事项都要有。"""
    found = [(index, segment.strip()) for index, segment in enumerate(segments)
             if segment.strip().startswith("下次核")]
    if not found:
        problems.append(Problem(path, line_number, "缺少「下次核」字段"))
        return
    if len(found) != 1 or found[0][0] != 1:
        problems.append(Problem(path, line_number, "「下次核」必须是产物栏第二段且只有一个"))
        return
    match = NEXT_CHECK_RE.fullmatch(found[0][1])
    if match is None:
        problems.append(Problem(path, line_number, "「下次核」格式应为 YYYY-MM-DD HH:MM"))
        return
    try:
        task.next_check_at = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M")
    except ValueError:
        problems.append(Problem(path, line_number, "「下次核」不是有效日期时间"))


def _parse_artifact(path, line_number, task, artifact, problems):
    """SKILL.md「契约」：产物栏第一段写谁在动。[ ] 是「在跑：<名字>（pid N）」（只写一个推进者，进程要还在）、
    「等：T 号」（多个用「、」）或「等：用户：…」；[x] 是「待验收：<产物指针>」。第二段交给 _parse_next_check。"""
    segments = re.split(r"[；;]", artifact)
    head = segments[0]
    if task.status == "x":
        if not head.startswith("待验收："):
            problems.append(Problem(path, line_number, "[x] 产物栏必须以「待验收：」开头"))
            return
        if not head[len("待验收："):].strip():
            problems.append(Problem(path, line_number, "[x] 「待验收：」后必须有产物指针"))
    elif head.startswith("在跑："):
        match = RUNNING_ENTRY_RE.fullmatch(head[len("在跑："):].strip())
        if match is None:
            problems.append(Problem(path, line_number, "「在跑：」只写一个推进者，格式「名字（pid N）」"))
        else:
            task.running_name = match.group(0)
            try:
                os.kill(int(match.group(1)), 0)
            except ProcessLookupError:
                problems.append(Problem(
                    path, line_number, f"在跑：{match.group(0)}的进程已不在：收尾或送验",
                ))
            except PermissionError:
                pass  # 别的用户的进程，kill(0) 拒绝但进程在
    elif head.startswith("等："):
        target = head[len("等："):].strip()
        if target.startswith("用户："):
            if not target[len("用户："):].strip():
                problems.append(Problem(path, line_number, "「等：用户：」后必须写待决定事项"))
        else:
            match = WAIT_TARGETS_RE.fullmatch(target)
            if match is None:
                problems.append(Problem(path, line_number, "「等：」后应为条目 ID 或「用户：…」"))
            else:
                task.wait_targets = re.split(r"\s*、\s*", match.group(0))
    else:
        problems.append(Problem(
            path, line_number, "[ ] 产物栏缺少「谁在动」前缀（应以「在跑：」或「等：」开头）",
        ))
        return
    _parse_next_check(path, line_number, segments, task, problems)


def _parse_contract(path, problems):
    """SKILL.md「契约」：清单文件头部写 session 和 cron_job_id，「## 清单」下每项四行：标题、判据、出处、产物；
    原话不写在这里。判据允许续行。"""
    lines = _read_lines(path, problems)
    if lines is None:
        return None

    list_indices = [i for i, line in enumerate(lines) if line.strip() == "## 清单"]
    list_index = list_indices[0] if list_indices else -1
    header_end = list_index if list_index >= 0 else next(
        (i for i, line in enumerate(lines) if line.startswith("## ")), len(lines),
    )
    header_lines = lines[:header_end]

    # 清单文件必须包含「## 清单」与 cron_job_id；原话在单独的文件里。
    if not list_indices:
        problems.append(Problem(path, 1, "缺少「## 清单」标题"))
    for index, line in enumerate(lines):
        if line.strip() == "## 用户原话":
            problems.append(Problem(
                path, _line_number(index),
                f"原话应写在 {_quotes_path(path).name}，清单文件里不能有「## 用户原话」",
            ))
    cron_rows = [(i, line) for i, line in enumerate(header_lines) if CRON_RE.match(line)]
    if not cron_rows:
        problems.append(Problem(path, 1, "缺少 cron_job_id: 行"))
    for cron_index, cron_line in cron_rows:
        if not cron_line.split(":", 1)[1].strip():
            problems.append(Problem(path, _line_number(cron_index), "cron_job_id: 值不能为空"))

    # 每份契约头都要记录 session，一个 session 只许一份契约。
    session_rows = [(i, match.group(1)) for i, line in enumerate(header_lines)
                    if (match := SESSION_RE.match(line))]
    session = ""
    if not session_rows:
        problems.append(Problem(path, 1, "缺少 session: 行"))
    else:
        session = session_rows[0][1].strip()
        if not session:
            problems.append(Problem(path, _line_number(session_rows[0][0]), "session: 值不能为空"))
        for duplicate, _ in session_rows[1:]:
            problems.append(Problem(path, _line_number(duplicate), "session: 行重复"))

    contract = Contract(path, session)

    checklist_start = list_index + 1 if list_index >= 0 else 0
    checklist_end = len(lines)
    index = checklist_start
    while index < checklist_end:
        task_match = TASK_RE.match(lines[index])
        if task_match is None:
            index += 1
            continue

        status, task_id = task_match.groups()
        if status not in (" ", "x"):
            problems.append(Problem(path, _line_number(index), "清单状态只能是 [ ] 或 [x]"))
            index += 1
            continue
        if TASK_ID_RE.fullmatch(task_id) is None:
            problems.append(Problem(path, _line_number(index), f"条目编号格式错误：{task_id}（应为 T 加编号）"))
        if task_id in contract.tasks:
            problems.append(Problem(path, _line_number(index), f"条目编号重复：{task_id}"))

        # 判据允许续行；条目由标题、完整判据、出处和产物构成。
        cursor = index + 1
        if cursor >= checklist_end or not lines[cursor].startswith("  - 判据："):
            problems.append(Problem(path, _line_number(cursor), f"条目 {task_id} 缺少「判据：」行"))
            index += 1
            continue
        if not lines[cursor][len("  - 判据："):].strip():
            problems.append(Problem(path, _line_number(cursor), "判据：内容不能为空"))
        cursor += 1
        while cursor < checklist_end and not lines[cursor].startswith("  - 出处："):
            continuation = lines[cursor]
            if (not continuation.strip()
                    or continuation.startswith("  - ")
                    or TASK_RE.match(continuation)
                    or continuation.startswith("## ")):
                break
            cursor += 1

        if cursor >= checklist_end or not lines[cursor].startswith("  - 出处："):
            problems.append(Problem(path, _line_number(cursor), f"条目 {task_id} 缺少「出处：」行"))
            index = max(index + 1, cursor)
            continue
        source_index = cursor
        artifact_index = source_index + 1
        if artifact_index >= checklist_end or not lines[artifact_index].startswith("  - 产物："):
            problems.append(Problem(path, _line_number(artifact_index), f"条目 {task_id} 缺少「产物：」行"))
            index = artifact_index
            continue

        # 每个事项都通过出处挂到原话；没有原话编号的出处挂不上。
        source_value = lines[source_index][len("  - 出处："):]
        source_ids = _source_numbers(source_value)
        if not source_value.strip():
            problems.append(Problem(path, _line_number(source_index), "出处：内容不能为空"))
        elif not source_ids:
            problems.append(Problem(path, _line_number(source_index), "出处至少含一个原话编号"))
        elif re.search(r"\d\s*[,，～]\s*\d", source_value):
            problems.append(Problem(path, _line_number(source_index), "出处里多个原话编号只认「、」"))

        task = Task(
            task_id=task_id,
            status=status,
            source_line=_line_number(source_index),
            artifact_line=_line_number(artifact_index),
            source_ids=source_ids,
        )
        _parse_artifact(
            path, _line_number(artifact_index), task,
            lines[artifact_index][len("  - 产物："):], problems,
        )
        contract.tasks[task_id] = task
        index = artifact_index + 1

    return contract


def _parse_landing(landing):
    """SKILL.md「原话怎么记」：落点三类，事项 T 号（多个用「、」）、「规矩：位置」、「无：原因」，类别间用「；」；
    说明文字里的 T 号不算落点。返回 (T 号集合, 错误说明列表)。"""
    targets = set()
    errors = []
    for raw_segment in landing.split("；"):
        segment = raw_segment.strip()
        if segment.startswith("规矩："):
            if not segment[len("规矩："):].strip():
                errors.append("规矩：后的说明不能为空")
            continue
        if segment.startswith("无："):
            if not segment[len("无："):].strip():
                errors.append("无：后的说明不能为空")
            continue

        task_ids = [item.strip() for item in segment.split("、")]
        if any(TASK_ID_RE.fullmatch(task_id) is None for task_id in task_ids):
            errors.append(f"落点类别格式错误：{segment}")
            continue
        targets.update(task_ids)
    return targets, errors


def _parse_quotes(path, problems):
    """SKILL.md「原话怎么记」：每段开头一行标注「（原话 N → 落点）」，从 1 连续编号，原话文件第一个非空行必须是标注；
    一条标注到下一条之间都是那条原话的正文。
    正文里顶格的「（原话 N → …）」也会被当成标注，这是接受的边界：用户贴契约片段时自己缩进一格。
    返回 {编号: {"line": 行号, "targets": 落点里的 T 号}}。"""
    lines = _read_lines(path, problems)
    if lines is None:
        return {}
    quotes = {}
    expected = 1
    first_line_checked = False
    for index, line in enumerate(lines):
        match = QUOTE_RE.match(line)
        if match is None:
            if line.strip() and not first_line_checked:
                problems.append(Problem(
                    path, _line_number(index), "原话文件第一个非空行必须是「（原话 N → 落点）」",
                ))
                first_line_checked = True
            continue
        first_line_checked = True
        number = int(match.group(1))
        if number != expected:
            problems.append(Problem(path, _line_number(index), f"原话编号应为 {expected}，实际是 {number}"))
        expected = number + 1
        landing = match.group(2).strip()
        if not landing:
            problems.append(Problem(path, _line_number(index), f"原话 {number} 缺少落点"))
            targets = set()
        else:
            targets, errors = _parse_landing(landing)
            for error in errors:
                problems.append(Problem(path, _line_number(index), f"原话 {number} {error}"))
        quotes[number] = {"line": _line_number(index), "targets": targets}
    return quotes


def _check_original_quotes(contract, quotes_path, quotes, ledger_ids, problems):
    """SKILL.md「契约」「原话怎么记」：每个事项通过出处挂到原话，落点与出处互相对得上——
    落点要命中清单或账本里的条目，出处引用的原话要存在且落回这个事项。"""
    for number, quote in quotes.items():
        for task_id in quote["targets"]:
            task = contract.tasks.get(task_id)
            if task is None and task_id not in ledger_ids:
                problems.append(Problem(
                    quotes_path, quote["line"],
                    f"原话 {number} 的落点 {task_id} 不在清单也不在账本",
                ))
            elif task is not None and number not in task.source_ids:
                problems.append(Problem(
                    quotes_path, quote["line"],
                    f"原话 {number} 落到 {task_id}，但 {task_id} 的出处没有原话 {number}",
                ))

    for task in contract.tasks.values():
        for number in sorted(task.source_ids):
            quote = quotes.get(number)
            if quote is None:
                problems.append(Problem(contract.path, task.source_line, f"出处引用的原话 {number} 不存在"))
            elif task.task_id not in quote["targets"]:
                problems.append(Problem(
                    contract.path, task.source_line,
                    f"{task.task_id} 的出处含原话 {number}，但原话落点没有 {task.task_id}",
                ))


def _check_wait_targets(contracts, problems):
    """SKILL.md「契约」：等待目标必须是同一份清单里的开放事项；[x] 待验收也算开放。"""
    for contract in contracts:
        for task in contract.tasks.values():
            for target in task.wait_targets:
                if target not in contract.tasks:
                    problems.append(Problem(
                        contract.path, task.artifact_line,
                        f"等：{target} 指向不在清单中的条目（可能已关闭或不存在）",
                    ))


def _check_wait_cycles(contracts, problems):
    """SKILL.md「契约」：等待链不能成环。成环就报环上的路径，记在环的起点那一项。"""
    for contract in contracts:
        tasks = contract.tasks
        state = {}  # 1 在栈上，2 已完成
        path = []

        def visit(task_id):
            state[task_id] = 1
            path.append(task_id)
            for target in tasks[task_id].wait_targets:
                if target not in tasks:
                    continue
                if state.get(target) == 1:
                    cycle = path[path.index(target):] + [target]
                    problems.append(Problem(
                        contract.path, tasks[target].artifact_line,
                        "等待链成环：" + " → ".join(cycle),
                    ))
                elif target not in state:
                    visit(target)
            path.pop()
            state[task_id] = 2

        for task_id in tasks:
            if task_id not in state:
                visit(task_id)


def _check_ledger(path, quotes, problems):
    """SKILL.md「契约」：账本一行一项，验收记录「- T1 …（指针；验收 …）」或取消记录「- [~] T2 …（原话 N：「摘句」）」，
    取消必须有原话依据，所以引用的原话要存在。返回账本里的 T 号。"""
    lines = _read_lines(path, problems)
    if lines is None:
        return set()
    task_ids = set()
    for index, line in enumerate(lines):
        if not line.strip():
            problems.append(Problem(path, _line_number(index), "账本格式错误（不允许空行）"))
            continue
        accepted = LEDGER_ACCEPTED_RE.fullmatch(line)
        cancelled = LEDGER_CANCELLED_RE.fullmatch(line)
        if accepted is None and cancelled is None:
            problems.append(Problem(
                path, _line_number(index), "账本格式错误（应为验收记录或带原话编号的取消记录）",
            ))
            continue
        task_ids.add((accepted or cancelled).group(1))
        if cancelled is not None and int(cancelled.group(2)) not in quotes:
            problems.append(Problem(
                path, _line_number(index), f"取消记录引用的原话 {cancelled.group(2)} 不存在",
            ))
    return task_ids


def _check_next_checks(contracts, now, problems):
    """SKILL.md「契约」：下次核是本机时间，不超过 12 小时；过点了就报，直到核过真实状态再改时间。不看状态。"""
    for contract in contracts:
        for task in contract.tasks.values():
            if task.next_check_at is None:
                continue
            remaining_seconds = (task.next_check_at - now).total_seconds()
            if remaining_seconds > MAX_NEXT_CHECK_HOURS * 3600:
                message = f"{task.task_id}：下次核离现在超过 {MAX_NEXT_CHECK_HOURS} 小时"
            elif remaining_seconds < 0:
                message = f"{task.task_id}：下次核 {task.next_check_at:%Y-%m-%d %H:%M} 已过期"
            else:
                continue
            problems.append(Problem(contract.path, task.artifact_line, message))


def _print_summary(contracts):
    """通过时一行：每份契约的 session、文件名和在跑的推进者（去重）。"""
    if not contracts:
        print("通过：无在办契约")
        return
    parts = []
    for contract in sorted(contracts, key=lambda contract: contract.session):
        running_names = list(dict.fromkeys(
            task.running_name for task in contract.tasks.values() if task.running_name
        ))
        running = "、".join(running_names) if running_names else "无"
        parts.append(f"session={contract.session}：{contract.path.name}；在跑：{running}")
    print("通过：" + "；".join(parts))


def check_directory(directory):
    """查目录里每份契约的三个文件和它们之间的引用；有问题就逐条写到 stderr 并返回 1，否则打印摘要返回 0。"""
    problems = []
    contracts = []
    for path in _find_contract_files(directory):
        contract = _parse_contract(path, problems)
        if contract is None:
            continue
        contracts.append(contract)
        quotes_path = _quotes_path(path)
        if not quotes_path.exists():
            # 原话都没有，落点和账本里的原话引用都没法核，只报缺文件。
            problems.append(Problem(path, 1, f"缺少原话文件 {quotes_path.name}"))
            continue
        quotes = _parse_quotes(quotes_path, problems)
        ledger_path = _ledger_path(path)
        ledger_ids = _check_ledger(ledger_path, quotes, problems) if ledger_path.exists() else set()
        _check_original_quotes(contract, quotes_path, quotes, ledger_ids, problems)

    # 一个 session 只对应一份在办契约；对每份重复契约都报告另一份的位置。
    contracts_by_session = {}
    for contract in contracts:
        if contract.session:
            contracts_by_session.setdefault(contract.session, []).append(contract)
    for session, members in contracts_by_session.items():
        if len(members) < 2:
            continue
        for contract in members:
            other = next(member for member in members if member.path != contract.path)
            problems.append(Problem(
                contract.path, 1,
                f"session {session} 已有另一份契约 {other.path.name}，一个会话只许一份",
            ))

    _check_wait_targets(contracts, problems)
    _check_wait_cycles(contracts, problems)
    _check_next_checks(contracts, datetime.now(), problems)

    problems.sort(key=lambda problem: (problem.path.name, problem.line, problem.message))
    if problems:
        for problem in problems:
            print(f"{problem.path.name}:{problem.line}: {problem.message}", file=sys.stderr)
        return 1
    _print_summary(contracts)
    return 0


def main():
    """用法：python3 check.py <契约目录>；目录必须显式给出。"""
    if len(sys.argv) != 2:
        print("用法：python3 check.py <契约目录>；必须显式提供契约目录", file=sys.stderr)
        return 2
    directory = Path(sys.argv[1]).expanduser()
    if not directory.exists() or not directory.is_dir():
        print(f"契约目录不存在或不是目录：{directory}", file=sys.stderr)
        return 2
    return check_directory(directory)


if __name__ == "__main__":
    raise SystemExit(main())
