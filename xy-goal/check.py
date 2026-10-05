#!/usr/bin/env python3
"""检查 xy-goal 在办契约的格式、预算、原话索引与账本。"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import sys


TOTAL_LIMIT = 5000
ITEM_LIMIT = 300

TASK_RE = re.compile(r"^\s*-\s*\[([^\]]*)\]\s*(\S+)(?:\s+.*)?$")
TASK_ID_RE = re.compile(r"T\d+[①②③④⑤⑥⑦⑧⑨⑩]?")
WAIT_TARGET_ID_PATTERN = r"T\d+[①②③④⑤⑥⑦⑧⑨⑩]?"
WAIT_TARGETS_RE = re.compile(
    rf"^({WAIT_TARGET_ID_PATTERN}(?:\s*[,，]\s*{WAIT_TARGET_ID_PATTERN})*)(?=$|[；;。\s])"
)
QUOTE_START_RE = re.compile(r"^\s*（原话\s+\d+\s*→")
QUOTE_RE = re.compile(r"^\s*（原话\s+(\d+)\s*→\s*(.*?)）")
SOURCE_RE = re.compile(r"原话\s+(\d+(?:(?:\s*～\s*\d+)|(?:\s*[、,，]\s*\d+))*)")
CRON_RE = re.compile(r"^\s*cron_job_id\s*:")
SESSION_RE = re.compile(r"^\s*session\s*:\s*(.*?)\s*$")
RUNNING_ENTRY_RE = re.compile(r"^(.+?)（pid ([1-9]\d*)）$")

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


@dataclass
class Contract:
    path: Path
    lines: list
    session: str
    list_chars: int
    list_line: int
    tasks: dict = field(default_factory=dict)


def _line_body(line):
    """去掉物理行尾，保留该行其余字符。"""
    return line.rstrip("\r\n")


def _line_number(index):
    return index + 1


def _expand_quote_numbers(value):
    """展开「原话 113～115、120」中的编号；返回编号与格式问题。"""
    numbers = set()
    malformed = []
    for match in SOURCE_RE.finditer(value):
        for component in re.split(r"\s*[、,，]\s*", match.group(1)):
            endpoints = re.split(r"\s*～\s*", component)
            if len(endpoints) == 1:
                numbers.add(int(endpoints[0]))
                continue
            if len(endpoints) != 2:
                malformed.append(component)
                continue
            first, last = map(int, endpoints)
            if last < first:
                malformed.append(component)
                continue
            numbers.update(range(first, last + 1))
    return numbers, malformed


def _is_task_id(value):
    return TASK_ID_RE.fullmatch(value) is not None


def _find_contract_files(directory):
    """只检查目录顶层的在办契约；账本由单独检查处理。"""
    return sorted(
        (path for path in directory.glob("*.md") if not path.name.endswith(".done.md")),
        key=lambda path: path.name,
    )


def _find_ledger_files(directory):
    return sorted(directory.glob("*.done.md"), key=lambda path: path.name)


def _read_lines(path, problems):
    try:
        # newline="" 保留原始换行符，预算按原文件的 Unicode 字符计数。
        with path.open("r", encoding="utf-8", newline="") as stream:
            return stream.read().splitlines(keepends=True)
    except (OSError, UnicodeError) as error:
        problems.append(Problem(path, 1, f"无法读取文件：{error}"))
        return None


def _collect_ancestor_processes():
    """读取祖先链的可执行文件名，并标出 Claude Code 主会话进程。"""
    ancestor_pids = set()
    main_session_pids = set()
    executable_errors = {}
    pid = os.getpid()
    while pid > 0 and pid not in ancestor_pids:
        ancestor_pids.add(pid)

        executable_path = Path("/proc") / str(pid) / "exe"
        try:
            executable = os.readlink(executable_path)
        except OSError as error:
            executable_errors[pid] = (
                f"提示：无法读取 {executable_path}：{type(error).__name__}: {error}；"
                "跳过该 PID 的主会话身份检查"
            )
        else:
            if executable.endswith(" (deleted)"):
                executable = executable[:-len(" (deleted)")]
            if Path(executable).name in {"claude", "claude.exe"}:
                main_session_pids.add(pid)

        if pid == 1:
            return ancestor_pids, main_session_pids, executable_errors, ""

        stat_path = Path("/proc") / str(pid) / "stat"
        try:
            stat = stat_path.read_text(encoding="ascii")
        except (OSError, UnicodeError) as error:
            return None, set(), {}, (
                f"提示：无法读取 {stat_path}：{type(error).__name__}: {error}；"
                "跳过自身会话 PID 检查"
            )

        closing_paren = stat.rfind(")")
        if closing_paren < 0:
            return None, set(), {}, (
                f"提示：无法解析 {stat_path}：缺少进程名结束括号；"
                "跳过自身会话 PID 检查"
            )
        fields = stat[closing_paren + 1:].split()
        if len(fields) < 2:
            return None, set(), {}, (
                f"提示：无法解析 {stat_path}：缺少 ppid 字段；"
                "跳过自身会话 PID 检查"
            )
        try:
            parent_pid = int(fields[1])
        except ValueError:
            return None, set(), {}, (
                f"提示：无法解析 {stat_path}：ppid 不是整数；"
                "跳过自身会话 PID 检查"
            )
        if parent_pid <= 0:
            return ancestor_pids, main_session_pids, executable_errors, ""
        pid = parent_pid

    return ancestor_pids, main_session_pids, executable_errors, ""


def _parse_artifact(path, line_number, status, artifact, problems, process_chain, notices):
    """检查产物栏格式，并提取运行名称或等待目标。"""
    ancestor_pids, main_session_pids, executable_errors = process_chain
    if status == "x":
        if not artifact.startswith("待验收："):
            problems.append(Problem(path, line_number, "[x] 产物栏必须以「待验收：」开头"))
        elif not artifact[len("待验收："):].strip():
            problems.append(Problem(path, line_number, "[x] 「待验收：」后必须有产物指针"))
        return "", []

    if artifact.startswith("在跑："):
        running_name = re.split(r"[；;]", artifact[len("在跑："):], maxsplit=1)[0].strip()
        if not running_name:
            problems.append(Problem(
                path, line_number,
                "「在跑：」要写进程号（pid N），脚本才能核它还活着",
            ))
            return "", []

        pid_missing = False
        for entry in running_name.split("、"):
            match = RUNNING_ENTRY_RE.fullmatch(entry.strip())
            if match is None:
                pid_missing = True
                continue
            name, pid_text = match.groups()
            pid = int(pid_text)
            if ancestor_pids is not None and pid in ancestor_pids:
                if pid in main_session_pids:
                    problems.append(Problem(
                        path, line_number,
                        f"在跑：{name}（pid {pid}）是运行本检查会话的 "
                        "Claude Code 主会话："
                        "主会话只编排，能派的派给子代理，否则写「等：…」",
                    ))
                    continue
                if pid in executable_errors:
                    notices.add(executable_errors[pid])
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                problems.append(Problem(
                    path, line_number,
                    f"在跑：{name}（pid {pid}）的进程已不在：收尾、送验或重派",
                ))
            except PermissionError:
                pass

        if pid_missing:
            problems.append(Problem(
                path, line_number,
                "「在跑：」要写进程号（pid N），脚本才能核它还活着",
            ))
        return running_name, []

    if artifact.startswith("等："):
        target = artifact[len("等："):].strip()
        if target.startswith("用户："):
            if not target[len("用户："):].strip():
                problems.append(Problem(path, line_number, "「等：用户：」后必须写待决定事项"))
            return "", []
        match = WAIT_TARGETS_RE.match(target)
        if match is None:
            problems.append(Problem(path, line_number, "「等：」后应为条目 ID 或「用户：…」"))
            return "", []
        wait_targets = re.split(r"\s*[,，]\s*", match.group(1))
        return "", wait_targets

    problems.append(Problem(
        path, line_number,
        "[ ] 产物栏缺少「谁在动」前缀（应以「在跑：」或「等：」开头）",
    ))
    return "", []


def _parse_contract(path, problems, process_chain, notices):
    raw_lines = _read_lines(path, problems)
    if raw_lines is None:
        return None
    lines = [_line_body(line) for line in raw_lines]

    list_indices = [i for i, line in enumerate(lines) if line.strip() == "## 清单"]
    quote_indices = [i for i, line in enumerate(lines) if line.strip() == "## 用户原话"]
    list_index = list_indices[0] if list_indices else -1
    quote_index = quote_indices[0] if quote_indices else -1
    header_end = list_index if list_index >= 0 else next(
        (i for i, line in enumerate(lines) if line.startswith("## ")), len(lines),
    )
    header_lines = lines[:header_end]

    # 契约必须包含清单、原话区与 cron_job_id。
    if not list_indices:
        problems.append(Problem(path, 1, "缺少「## 清单」标题"))
    if not quote_indices:
        problems.append(Problem(path, 1, "缺少「## 用户原话」标题"))
    for duplicate in list_indices[1:]:
        problems.append(Problem(path, _line_number(duplicate), "「## 清单」标题重复"))
    for duplicate in quote_indices[1:]:
        problems.append(Problem(path, _line_number(duplicate), "「## 用户原话」标题重复"))
    if list_index >= 0 and quote_index >= 0 and list_index > quote_index:
        problems.append(Problem(path, _line_number(list_index), "「## 清单」必须位于「## 用户原话」之前"))
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

    # 预算统计 ## 用户原话 之前的文本，包含模板头与换行。
    count_until = quote_index if quote_index >= 0 else len(raw_lines)
    list_chars = len("".join(raw_lines[:count_until]))
    list_line = _line_number(list_index if list_index >= 0 else 0)
    contract = Contract(path, lines, session, list_chars, list_line)

    checklist_start = list_index + 1 if list_index >= 0 else 0
    checklist_end = quote_index if quote_index >= checklist_start else len(lines)
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
        if not _is_task_id(task_id):
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

        item_text = "".join(raw_lines[index:artifact_index + 1])
        if item_text.endswith("\r\n"):
            item_text = item_text[:-2]
        elif item_text.endswith(("\n", "\r")):
            item_text = item_text[:-1]
        item_chars = len(item_text)
        if item_chars > ITEM_LIMIT:
            problems.append(Problem(
                path, _line_number(index),
                f"条目合计 {item_chars} 字，超过 {ITEM_LIMIT} 字",
            ))

        source_value = lines[source_index][len("  - 出处："):]
        if not source_value.strip():
            problems.append(Problem(path, _line_number(source_index), "出处：内容不能为空"))
        source_ids, malformed = _expand_quote_numbers(source_value)
        for component in malformed:
            problems.append(Problem(path, _line_number(source_index), f"出处中的原话区间格式错误：{component}"))

        artifact_value = lines[artifact_index][len("  - 产物："):]
        running_name, wait_targets = _parse_artifact(
            path, _line_number(artifact_index), status, artifact_value, problems,
            process_chain, notices,
        )
        contract.tasks[task_id] = Task(
            task_id=task_id,
            status=status,
            line=_line_number(index),
            source_line=_line_number(source_index),
            artifact_line=_line_number(artifact_index),
            source_ids=source_ids,
            wait_targets=wait_targets,
            running_name=running_name,
        )
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
        if not task_ids or any(not _is_task_id(task_id) for task_id in task_ids):
            errors.append(f"落点类别格式错误：{segment}")
            continue
        targets.update(task_ids)
    return targets, errors


def _check_original_quotes(contract, problems, session_tasks, ledger_ids):
    lines = contract.lines
    quote_headers = [i for i, line in enumerate(lines) if line.strip() == "## 用户原话"]
    if not quote_headers:
        return
    start = quote_headers[0] + 1
    next_header = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    origins = {}
    paragraph_start = True

    for index in range(start, next_header):
        body = lines[index]
        if not body.strip():
            paragraph_start = True
            continue
        if not paragraph_start:
            continue
        paragraph_start = False

        # 只把段首的「原话 N →」识别为标注；正文中的相似文字仍是原话内容。
        if not QUOTE_START_RE.match(body):
            continue
        match = QUOTE_RE.match(body)
        if match is None:
            problems.append(Problem(contract.path, _line_number(index), "原话标注格式错误，应为「（原话 N → 落点）」"))
            continue

        number = int(match.group(1))
        landing = match.group(2).strip()
        if not landing:
            problems.append(Problem(contract.path, _line_number(index), f"原话 {number} 缺少落点"))
            targets = set()
        else:
            targets, landing_errors = _parse_landing(landing)
            for error in landing_errors:
                problems.append(Problem(contract.path, _line_number(index), f"原话 {number} {error}"))
        if number in origins:
            problems.append(Problem(contract.path, _line_number(index), f"原话编号重复：{number}"))
            origins[number]["targets"].update(targets)
        else:
            origins[number] = {"line": _line_number(index), "targets": targets}

    # 原话落点必须命中同一 session 的在办条目或本契约账本中的条目。
    for number, origin in origins.items():
        for task_id in origin["targets"]:
            matching_tasks = session_tasks.get(task_id, [])
            if not matching_tasks and task_id not in ledger_ids:
                problems.append(Problem(
                    contract.path, origin["line"],
                    f"原话 {number} 的落点 {task_id} 不在清单也不在账本",
                ))
            elif matching_tasks and not any(number in task.source_ids for task in matching_tasks):
                problems.append(Problem(
                    contract.path, origin["line"],
                    f"原话 {number} 落到 {task_id}，但 {task_id} 的出处没有原话 {number}",
                ))

    # 出处引用的原话必须存在，且其落点必须包含对应的在办条目。
    for task in contract.tasks.values():
        for number in sorted(task.source_ids):
            origin = origins.get(number)
            if origin is None:
                problems.append(Problem(contract.path, task.source_line, f"出处引用的原话 {number} 不存在"))
            elif task.task_id not in origin["targets"]:
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
    for index, raw_line in enumerate(lines):
        line = _line_body(raw_line)
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
    ancestor_pids, main_session_pids, executable_errors, scan_notice = (
        _collect_ancestor_processes()
    )
    process_chain = (ancestor_pids, main_session_pids, executable_errors)
    notices = set()
    if scan_notice:
        notices.add(scan_notice)
    for path in _find_contract_files(directory):
        contract = _parse_contract(path, problems, process_chain, notices)
        if contract is not None:
            contracts.append(contract)

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

    tasks_by_session = {}
    for contract in contracts:
        session_tasks = tasks_by_session.setdefault(contract.session, {})
        for task_id, task in contract.tasks.items():
            session_tasks.setdefault(task_id, []).append(task)
    for contract in contracts:
        expected_ledger = f"{contract.path.stem}.done.md"
        _check_original_quotes(
            contract,
            problems,
            tasks_by_session.get(contract.session, {}),
            ledger_ids.get(expected_ledger, set()),
        )
    _check_wait_targets(contracts, problems)
    _check_wait_cycles(contracts, problems)
    _check_budgets(contracts, problems)

    for notice in sorted(notices):
        print(notice, file=sys.stderr)
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
