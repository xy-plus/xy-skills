import json
import re
import sys
from datetime import datetime
from pathlib import Path

from goal_common import (
    ITEM_LIMIT,
    LINE_BREAKS,
    TASK_FIELDS,
    TOTAL_LIMIT,
    Contract,
    GoalError,
    automatic_reason,
    encode_fields,
    encode_links,
    parse_links,
    quote_files,
    read_text,
    require_state,
    require_text,
    session_project,
    write_files,
)


def require_source_quotes(sources, quotes, contract):
    for number in sources:
        if number not in quotes:
            raise GoalError(f"原话 {number} 不存在：{contract / 'quotes'}")
        reason = automatic_reason(quotes[number], contract)
        if reason is not None:
            raise GoalError(f"原话 {number} 是机器输入（{reason}），不能作为出处")

def read_session_transcript(session_id):
    project = session_project(session_id)
    path = Path.home() / ".claude" / "projects" / project / f"{session_id}.jsonl"
    records = []
    # 逐行解析并立即丢弃无关字段，避免把数百 MB 的会话记录整体载入内存。
    # Claude Code 正在追加的末行没有 LF，跳过它；此前每行都必须完整且可解析。
    with path.open("rb") as transcript:
        for line_number, row in enumerate(transcript, start=1):
            if not row.endswith(b"\n"):
                break
            try:
                source = json.loads(row.decode("utf-8"))
                if not isinstance(source, dict):
                    raise ValueError("记录不是对象")
            except (UnicodeError, ValueError) as error:
                raise GoalError(f"会话记录第 {line_number} 行无法解析：{error}") from error

            uuid = source.get("uuid")
            if (source.get("isSidechain") or not isinstance(uuid, str) or not uuid
                    or any(char in uuid for char in LINE_BREAKS)):
                continue
            record = {"uuid": uuid}
            for field in ("parentUuid", "type", "timestamp"):
                value = source.get(field)
                if isinstance(value, str):
                    record[field] = value
            prompt_id = source.get("promptId")
            if "promptId" in source and (isinstance(prompt_id, str) or prompt_id is None):
                record["promptId"] = prompt_id
            origin = source.get("origin")
            if isinstance(origin, dict):
                kind = origin.get("kind")
                if isinstance(kind, str):
                    record["origin_kind"] = kind
            message = source.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if source.get("type") == "user" and isinstance(content, str):
                record["content"] = content
            records.append(record)
    return records

def transcript_time(record):
    value = record.get("timestamp")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.timestamp()

def withdrawn_quote_dismissals(contract, transcript):
    # 撤回消息须同时符合三点：人工 user 文本有父节点；沿 parentUuid 没有 assistant 后代；之后有同父、promptId 不同的记录。
    # 同父而不同 promptId 表明对话从 W 的父节点继续，排除同轮排队插话和 tool_result；assistant 后代表示已获回复，之后回退也可能已产生事项。
    # 附件等非 assistant 子节点不算回复。
    #
    # N 是 W 之后第一条 content 为字符串的 user 记录。hook 在 W 的提交时间之后写原话文件、N 提交前完成；正文全等且 mtime 落在 [W.timestamp, N.timestamp) 才能配对。
    # 窗口内必须恰好一条原话；旧的漏记同文原话和之后重发的原话都在窗口外，已处理的原话不改。
    records = transcript
    rows_by_uuid = {}
    duplicated_uuids = set()
    for record in records:
        uuid = record["uuid"]
        if uuid in rows_by_uuid:
            duplicated_uuids.add(uuid)
        else:
            rows_by_uuid[uuid] = record
    if duplicated_uuids:
        records = [record for record in records if record["uuid"] not in duplicated_uuids]
        for uuid in duplicated_uuids:
            rows_by_uuid.pop(uuid, None)

    quote_numbers = {}
    quote_times = {}
    for number, text in contract.quotes.items():
        quote_numbers.setdefault(text, []).append(number)
        quote_times[number] = (contract.path / "quotes" / f"{number}.md").stat().st_mtime

    possible_withdrawals = {
        record["uuid"] for record in records
        if record.get("type") == "user" and record.get("origin_kind") == "human"
        and isinstance(record.get("content"), str)
        and isinstance(record.get("parentUuid"), str) and record["parentUuid"]
        and record["content"] in quote_numbers
    }
    answered = set()
    for record in records:
        if record.get("type") != "assistant" or not possible_withdrawals:
            continue
        parent = record.get("parentUuid")
        while isinstance(parent, str) and parent in rows_by_uuid and parent not in answered:
            answered.add(parent)
            parent = rows_by_uuid[parent].get("parentUuid")

    possible = {}
    for index, withdrawn in enumerate(records):
        if withdrawn.get("type") != "user":
            continue
        parent = withdrawn.get("parentUuid")
        content = withdrawn.get("content")
        if (withdrawn.get("origin_kind") != "human" or not isinstance(content, str)
                or not isinstance(parent, str) or not parent
                or withdrawn["uuid"] in answered or content not in quote_numbers):
            continue

        has_new_prompt_sibling = False
        next_user = None
        for later_index in range(index + 1, len(records)):
            later = records[later_index]
            if (later.get("parentUuid") == parent
                    and later.get("promptId") != withdrawn.get("promptId")):
                has_new_prompt_sibling = True
            if (next_user is None and later.get("type") == "user"
                    and isinstance(later.get("content"), str)):
                next_user = later
            if has_new_prompt_sibling and next_user is not None:
                break
        if not has_new_prompt_sibling:
            continue
        if next_user is None:
            continue
        start, end = transcript_time(withdrawn), transcript_time(next_user)
        if start is None or end is None:
            continue

        matches = [number for number in quote_numbers[content]
                   if start <= quote_times[number] < end]
        if len(matches) == 1:
            possible.setdefault(matches[0], set()).add(withdrawn["uuid"])

    handled = {number for number, _ in contract.links} | contract.dismissed.keys()
    return {number: f"自动：已撤回（会话记录 {next(iter(uuids))}）"
            for number, uuids in possible.items()
            if number not in handled and len(uuids) == 1}

def relink(path, goal_content, task, sources):
    """唯一先写后整体校验的入口：多个无出处事项须逐项修，进度不能因剩余错误回滚。

    不并入 update，保持正常命令先校验、拒绝时不写入的含义。
    """
    if (task is None) != (sources is None):
        raise GoalError("事项与 --source 必须同时给或同时不给")
    try:
        contract = Contract(path, goal_content)
    except GoalError:
        pass
    else:
        has_closed_machine_source = any(
            landing in contract.done and automatic_reason(contract.quotes[number], path) is not None
            for number, landing in contract.links
        )
        if task is None and has_closed_machine_source:
            print(f"已关闭事项的历史出处保留：{path}")
            return 0
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
        require_source_quotes(sources, quotes, path)
    links, dismissed, invalid = parse_links(text, quotes, tasks | done)
    for line, reason in invalid:
        print(f"丢弃：{line}；{reason}")
    kept_links = set()
    for number, landing in links:
        if landing not in tasks:
            # 只修开放事项的机器输入映射；关闭事项的记录已经是历史。
            kept_links.add((number, landing))
            continue
        reason = automatic_reason(quotes[number], path)
        if reason is None:
            kept_links.add((number, landing))
        else:
            print(f"丢弃：{number} {landing}；原话 {number} 是机器输入（{reason}），不能作为出处")
            dismissed[number] = reason
    links = kept_links
    if task is not None:
        links.update((number, task) for number in sources)
    write_files({links_path: encode_links(links, dismissed)}, [])
    Contract(path, goal_content)
    print(f"修复完成：{path}")
    return 0


def show(contract):
    lines = [f"目标：{contract.metadata['target']}"]
    warnings = []
    sources_by_task = {task: [] for task in contract.tasks}
    for number, task in contract.links:
        if task in sources_by_task:
            sources_by_task[task].append(number)
    for sources in sources_by_task.values():
        sources.sort()
    for task, values in contract.tasks.items():
        # 保持清单每项四行；原值中的换行在此显示为转义，不改文件里的值。
        display = {key: value.translate({ord(char): json.dumps(char, ensure_ascii=True)[1:-1]
                                         for char in LINE_BREAKS})
                   for key, value in values.items()}
        provenance = []
        if sources_by_task[task]:
            provenance.append("原话 " + "、".join(map(str, sources_by_task[task])))
        if "evidence" in values:
            provenance.append("作者发起：" + display["evidence"])
        item = (f"{task} {display['title']}\n  判据：{display['criteria']}\n"
                f"  出处：{'；'.join(provenance)}\n"
                f"  状态：{display['state']}；指针：{display['pointer']}")
        lines.append(item)
        if len(item) > ITEM_LIMIT:
            warnings.append(f"提示：{task} 四行 {len(item)} 字，超过 {ITEM_LIMIT} 字，请压短")
    lines.append("还没处理的原话：" + ("、".join(map(str, contract.pending())) or "无"))
    text = "\n".join(lines) + "\n"
    if len(text) > TOTAL_LIMIT:
        warnings.append(f"提示：清单全文 {len(text)} 字，超过 {TOTAL_LIMIT} 字，请压短")
    print(text + "".join(warning + "\n" for warning in warnings), end="")


def task_sources(contract, task):
    return sorted(number for number, target in contract.links if target == task)


def require_task(contract, task):
    if task not in contract.tasks:
        raise GoalError(f"开放事项 {task} 不存在")



def change(contract, options):
    command = options.command
    changes, removals = {}, []
    if command == "add":
        values = {name: getattr(options, name) for name in TASK_FIELDS.values()}
        if options.source_kind == "作者发起":
            if options.evidence is None:
                raise GoalError("作者发起事项必须给 --evidence")
            require_text(options.evidence, "证据")
            if options.evidence == "无":
                raise GoalError("作者发起证据不能写「无」")
            values["evidence"] = options.evidence
        elif options.evidence is not None:
            raise GoalError("原话直提事项不能给 --evidence")
        for label, name in TASK_FIELDS.items():
            require_text(values[name], label)
        require_state(values["state"])
        require_source_quotes(options.source, contract.quotes, contract.path)
        number = max([int(task[1:]) for task in contract.tasks.keys() | contract.done.keys()], default=0) + 1
        task = f"T{number}"
        contract.links.update((number, task) for number in options.source)
        changes[contract.path / "tasks" / f"{task}.md"] = encode_fields(values)
    elif command == "update":
        task = options.task
        require_task(contract, task)
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
            require_source_quotes(options.source, contract.quotes, contract.path)
            contract.links.update((number, task) for number in options.source)
        if updated_values:
            contract.tasks[task].update(updated_values)
            changes[contract.path / "tasks" / f"{task}.md"] = encode_fields(contract.tasks[task])
    elif command == "dismiss":
        require_source_quotes([options.quote], contract.quotes, contract.path)
        require_text(options.reason, "原因")
        if any(char in options.reason for char in LINE_BREAKS):
            raise GoalError("原因必须写成一行")
        targets = sorted(target for number, target in contract.links if number == options.quote)
        if targets:
            raise GoalError(f"原话 {options.quote} 已挂事项 " + "、".join(targets))
        contract.dismissed[options.quote] = options.reason
    elif command == "reopen":
        task = options.task
        if task not in contract.done:
            raise GoalError(f"已关闭事项 {task} 不存在")
        cancel_quote = contract.done[task].get("cancel_quote")
        sources = task_sources(contract, task)
        if cancel_quote is not None:
            sources = [number for number in sources if number != int(cancel_quote)]
        if not sources:
            detail = "除取消依据外没有出处" if cancel_quote is not None else "没有出处"
            raise GoalError(f"已关闭事项 {task} {detail}，不能重开")
        # 只恢复事项字段并丢弃旧结论；若取消依据是原话，它的映射仍保留在 links.md。
        values = {name: contract.done[task][name] for name in TASK_FIELDS.values()}
        if "evidence" in contract.done[task]:
            values["evidence"] = contract.done[task]["evidence"]
        changes[contract.path / "tasks" / f"{task}.md"] = encode_fields(values)
        removals.append(contract.path / "done" / f"{task}.md")
    else:
        task = options.task
        require_task(contract, task)
        if command == "accept":
            require_text(options.verdict, "验收结论")
            conclusion = {"verdict": options.verdict}
        else:
            sources = task_sources(contract, task)
            if options.reason is not None:
                if "evidence" not in contract.tasks[task]:
                    raise GoalError("没有作者发起证据的事项必须用 --quote")
                require_text(options.reason, "取消原因")
                conclusion = {"cancel_reason": options.reason}
            else:
                if not sources:
                    raise GoalError("开放事项没有原话出处，不能用 --quote")
                require_source_quotes([options.quote], contract.quotes, contract.path)
                conclusion = {"cancel_quote": str(options.quote)}
                contract.links.add((options.quote, task))
        original = contract.path / "tasks" / f"{task}.md"
        # 统一编码已解析字段与结论，不依赖被接受的原文件是否带末尾 LF。
        changes[contract.path / "done" / f"{task}.md"] = encode_fields({**contract.tasks[task], **conclusion})
        removals.append(original)
    changes[contract.path / "links.md"] = encode_links(contract.links, contract.dismissed)
    write_files(changes, removals)
    if command == "add":
        print(task)


def check(contract, session_id):
    pending = contract.pending()
    if pending:
        try:
            automatic = withdrawn_quote_dismissals(
                contract, read_session_transcript(session_id))
        except (GoalError, OSError) as error:
            print(f"提示：未做撤回对账：{error}", file=sys.stderr)
        else:
            if automatic:
                contract.dismissed.update(automatic)
                write_files({contract.path / "links.md": encode_links(
                    contract.links, contract.dismissed)}, [])
        pending = contract.pending()
    if pending:
        for number in pending:
            print(f"{contract.path / 'quotes' / f'{number}.md'}: 原话 {number} 还没处理", file=sys.stderr)
        return 1
    print(f"通过：{contract.path}")
    return 0
