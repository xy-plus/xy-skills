#!/usr/bin/env python3
"""检查 xy-goal 在办契约的格式、预算、原话索引与账本。"""

from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import re
import sys


TOTAL_LIMIT = 5000
ITEM_LIMIT = 300
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

LEDGER_ACCEPTED_RE = re.compile(
    r"^-\s+(T\d+[①②③④⑤⑥⑦⑧⑨⑩]?)\s+.+（.+；验收\s+[^）]+）$"
)
LEDGER_CANCELLED_RE = re.compile(
    r"^-\s+\[~\]\s+(T\d+[①②③④⑤⑥⑦⑧⑨⑩]?)\s+.+（原话\s+\d+：「[^」]+」）$"
)


@dataclass
class Problem:
    path: Path
    line: int
    message: str


@dataclass
class Task:
    task_id: str
    status: str
    line: int
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
    list_chars: int
    list_line: int
    tasks: dict = field(default_factory=dict)


def _line_number(index):
    return index + 1


def _source_numbers(value):
    """出处里的原话编号，多个用「、」分隔。"""
    numbers = set()
    for match in SOURCE_RE.finditer(value):
        numbers.update(int(part) for part in re.split(r"\s*、\s*", match.group(1)))
    return numbers


def _find_contract_files(directory):
    """清单文件：目录顶层、不是原话也不是账本的 *.md。"""
    return sorted(
        (path for path in directory.glob("*.md")
         if not path.name.endswith((".quotes.md", ".done.md"))),
        key=lambda path: path.name,
    )


def _find_ledger_files(directory):
    return sorted(directory.glob("*.done.md"), key=lambda path: path.name)


def _quotes_path(contract_path):
    return contract_path.with_name(contract_path.stem + ".quotes.md")


def _ledger_path(contract_path):
    return contract_path.with_name(contract_path.stem + ".done.md")


def _read_lines(path, problems):
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        problems.append(Problem(path, 1, f"无法读取文件：{error}"))
        return None


def _parse_next_check(path, line_number, segments, task, problems):
    """产物栏第二段必须是且只有一个「下次核：YYYY-MM-DD HH:MM」。"""
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
    """产物栏：第一段写谁在动，第二段写下次核。"""
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

    # 每份契约头都要记录 session，预算按该值分组。
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

    list_chars = len("\n".join(lines))
    list_line = _line_number(list_index if list_index >= 0 else 0)
    contract = Contract(path, session, list_chars, list_line)

    checklist_start = list_index + 1 if list_index >= 0 else 0
    checklist_end = len(lines)
    index = checklist_start
    while index < checklist_end:
        body = lines[index]
        stripped = body.strip()
        if stripped.startswith(("已验收：", "已取消：")) or re.match(
                r"^##\s*(已验收|已取消)(?:\s|$)", stripped):
            # 已验收或已取消内容应移到同目录账本，不留在在办清单。
            problems.append(Problem(path, _line_number(index), "已验收或已取消内容应移入 .done.md 账本"))

        task_match = TASK_RE.match(body)
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
        criteria_rows = []
        if cursor >= checklist_end or not lines[cursor].startswith("  - 判据："):
            problems.append(Problem(path, _line_number(cursor), f"条目 {task_id} 缺少「判据：」行"))
            index += 1
            continue
        if not lines[cursor][len("  - 判据："):].strip():
            problems.append(Problem(path, _line_number(cursor), "判据：内容不能为空"))
        criteria_rows.append(lines[cursor])
        cursor += 1
        while cursor < checklist_end and not lines[cursor].startswith("  - 出处："):
            continuation = lines[cursor]
            if (not continuation.strip()
                    or continuation.startswith("  - ")
                    or TASK_RE.match(continuation)
                    or continuation.startswith("## ")):
                break
            criteria_rows.append(continuation)
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

        item_chars = len("\n".join(lines[index:artifact_index + 1]))
        if item_chars > ITEM_LIMIT:
            problems.append(Problem(
                path, _line_number(index),
                f"条目合计 {item_chars} 字，超过 {ITEM_LIMIT} 字",
            ))

        # 每个事项都通过出处挂到原话；没有原话编号的出处挂不上。
        source_value = lines[source_index][len("  - 出处："):]
        source_ids = _source_numbers(source_value)
        if not source_value.strip():
            problems.append(Problem(path, _line_number(source_index), "出处：内容不能为空"))
        elif not source_ids:
            problems.append(Problem(path, _line_number(source_index), "出处至少含一个原话编号"))

        task = Task(
            task_id=task_id,
            status=status,
            line=_line_number(index),
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
    """按类别解析原话落点；解释文字中的 T 编号不视作任务号。"""
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
    """原话文件：第一个非空行必须是标注，编号从 1 连续；一条标注到下一条之间都是那条原话的正文。
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
    """原话落点与条目出处互相对得上：落点要命中清单或账本里的条目，出处引用的原话要存在且落回来。"""
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
    # 等：列出的每个条目都必须仍在同一 session 的清单中。
    tasks_by_session = {}
    for contract in contracts:
        tasks_by_session.setdefault(contract.session, set()).update(contract.tasks)
    for contract in contracts:
        open_tasks = tasks_by_session.get(contract.session, set())
        for task in contract.tasks.values():
            for target in task.wait_targets:
                if target not in open_tasks:
                    problems.append(Problem(
                        contract.path, task.artifact_line,
                        f"等：{target} 指向不在清单中的条目（可能已关闭或不存在）",
                    ))


def _check_wait_cycles(contracts, problems):
    # 把每个等待目标作为边，报告所有等待环中的条目。
    tasks_by_session = {}
    for contract in contracts:
        session_tasks = tasks_by_session.setdefault(contract.session, {})
        for task in contract.tasks.values():
            session_tasks.setdefault(task.task_id, []).append((contract, task))

    for session_tasks in tasks_by_session.values():
        # 重复 ID 已由其他格式检查报告；只沿唯一 ID 的边检查，避免猜测目标。
        unique_tasks = {
            task_id: matches[0]
            for task_id, matches in session_tasks.items()
            if len(matches) == 1
        }
        graph = {
            task_id: [target for target in task.wait_targets if target in unique_tasks]
            for task_id, (_, task) in unique_tasks.items()
        }
        next_index = 0
        indices = {}
        lowlinks = {}
        stack = []
        on_stack = set()
        components = []

        def visit(task_id):
            nonlocal next_index
            indices[task_id] = next_index
            lowlinks[task_id] = next_index
            next_index += 1
            stack.append(task_id)
            on_stack.add(task_id)

            for target in graph[task_id]:
                if target not in indices:
                    visit(target)
                    lowlinks[task_id] = min(lowlinks[task_id], lowlinks[target])
                elif target in on_stack:
                    lowlinks[task_id] = min(lowlinks[task_id], indices[target])

            if lowlinks[task_id] == indices[task_id]:
                component = set()
                while True:
                    member = stack.pop()
                    on_stack.remove(member)
                    component.add(member)
                    if member == task_id:
                        break
                components.append(component)

        for task_id in graph:
            if task_id not in indices:
                visit(task_id)

        for component in components:
            if len(component) == 1:
                only_task = next(iter(component))
                if only_task not in graph[only_task]:
                    continue

            start_id = next(task_id for task_id in graph if task_id in component)
            contract, task = unique_tasks[start_id]
            cycle_path = [start_id]
            visited = {start_id}

            def find_cycle(task_id):
                for target in graph[task_id]:
                    if target not in component:
                        continue
                    if target == start_id:
                        cycle_path.append(start_id)
                        return True
                    if target in visited:
                        continue
                    visited.add(target)
                    cycle_path.append(target)
                    if find_cycle(target):
                        return True
                    cycle_path.pop()
                return False

            find_cycle(start_id)
            cycle_description = " → ".join(cycle_path)
            cycle_members = "、".join(sorted(component))
            problems.append(Problem(
                contract.path, task.artifact_line,
                f"等待链成环：{cycle_description}；环上条目：{cycle_members}",
            ))


def _check_ledger(path, problems):
    lines = _read_lines(path, problems)
    if lines is None:
        return set()
    task_ids = set()
    for index, line in enumerate(lines):
        if not line.strip():
            problems.append(Problem(path, _line_number(index), "账本格式错误（不允许空行）"))
            continue
        # 账本只能记录已验收产物或有原话依据的取消事项。
        accepted = LEDGER_ACCEPTED_RE.fullmatch(line)
        cancelled = LEDGER_CANCELLED_RE.fullmatch(line)
        if accepted is None and cancelled is None:
            problems.append(Problem(
                path, _line_number(index),
                "账本格式错误（应为验收记录或带原话编号的取消记录）",
            ))
        else:
            task_ids.add((accepted or cancelled).group(1))
    return task_ids


def _check_budgets(contracts, problems):
    # 按 session 汇总所有在办契约的预算，拆分文件不会降低总量。
    groups = {}
    for contract in contracts:
        groups.setdefault(contract.session, []).append(contract)
    for session, members in groups.items():
        total = sum(contract.list_chars for contract in members)
        if total <= TOTAL_LIMIT:
            continue
        session_label = session if session else "（缺失）"
        used = 0
        location = members[-1]
        for contract in members:
            used += contract.list_chars
            if used > TOTAL_LIMIT:
                location = contract
                break
        problems.append(Problem(
            location.path, location.list_line,
            f"session {session_label} 的清单部分合计 {total} 字，超过 {TOTAL_LIMIT} 字上限",
        ))


def _local_now():
    return datetime.now()


def _check_next_checks(contracts, now, problems):
    """每个开放条目的下次核都在现在之后、12 小时之内，不看状态。"""
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
    groups = {}
    for contract in contracts:
        groups.setdefault(contract.session, []).append(contract)

    if not groups:
        print("通过：无在办契约；无 session 预算")
        return

    parts = []
    for session in sorted(groups):
        members = groups[session]
        total = sum(contract.list_chars for contract in members)
        files = "、".join(
            f"{contract.path.name} {contract.list_chars} 字" for contract in members
        )
        running_names = list(dict.fromkeys(
            task.running_name
            for contract in members
            for task in contract.tasks.values()
            if task.running_name
        ))
        running = "、".join(running_names) if running_names else "无"
        parts.append(
            f"session={session}：{files}；合计 {total}/{TOTAL_LIMIT} 字；在跑：{running}"
        )
    print("通过：" + "；".join(parts))


def check_directory(directory):
    problems = []
    contracts = []
    quotes_by_contract = {}
    for path in _find_contract_files(directory):
        contract = _parse_contract(path, problems)
        if contract is None:
            continue
        contracts.append(contract)
        quotes_path = _quotes_path(path)
        if quotes_path.exists():
            quotes_by_contract[path] = _parse_quotes(quotes_path, problems)
        else:
            problems.append(Problem(path, 1, f"缺少原话文件 {quotes_path.name}"))

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

    ledger_ids = {}
    for path in _find_ledger_files(directory):
        ledger_ids[path.name] = _check_ledger(path, problems)

    # 原话文件缺失时只报缺文件，落点没法核。
    for contract in contracts:
        if contract.path not in quotes_by_contract:
            continue
        _check_original_quotes(
            contract,
            _quotes_path(contract.path),
            quotes_by_contract[contract.path],
            ledger_ids.get(_ledger_path(contract.path).name, set()),
            problems,
        )
    _check_wait_targets(contracts, problems)
    _check_wait_cycles(contracts, problems)
    _check_budgets(contracts, problems)
    now = _local_now()
    _check_next_checks(contracts, now, problems)

    problems.sort(key=lambda problem: (problem.path.name, problem.line, problem.message))
    if problems:
        for problem in problems:
            print(f"{problem.path.name}:{problem.line}: {problem.message}", file=sys.stderr)
        return 1
    _print_summary(contracts)
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("用法：python3 check.py <契约目录>；必须显式提供契约目录", file=sys.stderr)
        return 2
    directory = Path(args[0]).expanduser()
    if not directory.exists() or not directory.is_dir():
        print(f"契约目录不存在或不是目录：{directory}", file=sys.stderr)
        return 2
    return check_directory(directory)


if __name__ == "__main__":
    raise SystemExit(main())
