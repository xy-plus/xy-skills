import hashlib
import json
import re
import os
import sys
from dataclasses import dataclass
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
    merge_inbox,
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
            record = transcript_record_for_matching(source)
            if record is not None:
                records.append(record)
    return records


def transcript_record_for_matching(source):
    uuid = source.get("uuid")
    if (source.get("isSidechain") or not isinstance(uuid, str) or not uuid
            or any(char in uuid for char in LINE_BREAKS)):
        return None
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
    return record

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
    records, rows_by_uuid = unique_transcript_records(transcript)
    quote_numbers, quote_times = contract_quote_occurrences(contract)
    possible_withdrawals = {
        record["uuid"] for record in records
        if record.get("type") == "user" and record.get("origin_kind") == "human"
        and isinstance(record.get("content"), str)
        and isinstance(record.get("parentUuid"), str) and record["parentUuid"]
        and record["content"] in quote_numbers
    }
    answered = assistant_answered_ancestors(records, rows_by_uuid, possible_withdrawals)
    possible = withdrawn_quotes_in_reply_windows(records, answered, quote_numbers, quote_times)
    handled = {number for number, _ in contract.links} | contract.dismissed.keys()
    return {number: f"自动：已撤回（会话记录 {next(iter(uuids))}）"
            for number, uuids in possible.items()
            if number not in handled and len(uuids) == 1}


def unique_transcript_records(transcript):
    rows_by_uuid = {}
    duplicated_uuids = set()
    for record in transcript:
        uuid = record["uuid"]
        if uuid in rows_by_uuid:
            duplicated_uuids.add(uuid)
        else:
            rows_by_uuid[uuid] = record
    if duplicated_uuids:
        records = [record for record in transcript if record["uuid"] not in duplicated_uuids]
        for uuid in duplicated_uuids:
            rows_by_uuid.pop(uuid, None)
    else:
        records = transcript
    return records, rows_by_uuid


def contract_quote_occurrences(contract):
    quote_numbers = {}
    quote_times = {}
    for number, text in contract.quotes.items():
        quote_numbers.setdefault(text, []).append(number)
        quote_times[number] = (contract.path / "quotes" / f"{number}.md").stat().st_mtime
    return quote_numbers, quote_times


def assistant_answered_ancestors(records, rows_by_uuid, possible_withdrawals):
    answered = set()
    for record in records:
        if record.get("type") != "assistant" or not possible_withdrawals:
            continue
        parent = record.get("parentUuid")
        while isinstance(parent, str) and parent in rows_by_uuid and parent not in answered:
            answered.add(parent)
            parent = rows_by_uuid[parent].get("parentUuid")
    return answered


def withdrawn_quotes_in_reply_windows(records, answered, quote_numbers, quote_times):
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

        has_new_prompt_sibling, next_user = reply_window(
            records, index, parent, withdrawn.get("promptId"))
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
    return possible


def reply_window(records, index, parent, prompt_id):
    has_new_prompt_sibling = False
    next_user = None
    for later in records[index + 1:]:
        if later.get("parentUuid") == parent and later.get("promptId") != prompt_id:
            has_new_prompt_sibling = True
        if next_user is None and later.get("type") == "user" and isinstance(later.get("content"), str):
            next_user = later
        if has_new_prompt_sibling and next_user is not None:
            break
    return has_new_prompt_sibling, next_user


def relink(path, goal_content, task, sources):
    """唯一先写后整体校验的入口：多个无出处事项须逐项修，进度不能因剩余错误回滚。

    不并入 update，保持正常命令先校验、拒绝时不写入的含义。
    """
    if (task is None) != (sources is None):
        raise GoalError("事项与 --source 必须同时给或同时不给")
    if relink_not_needed(path, goal_content, task):
        return 0
    text = read_text(path / "links.md")
    quotes = quote_files(path / "quotes")
    tasks, done = relink_task_files(path)
    validate_relink_request(task, sources, quotes, tasks, path)
    data = RelinkData(path, text, quotes, tasks, done)
    links, dismissed = repaired_mappings(data, task, sources)
    write_files({path / "links.md": encode_links(links, dismissed)}, [])
    Contract(path, goal_content)
    print(f"修复完成：{path}")
    return 0


def relink_not_needed(path, goal_content, task):
    try:
        contract = Contract(path, goal_content)
    except GoalError:
        return False
    has_closed_machine_source = any(
        landing in contract.done and automatic_reason(contract.quotes[number], path) is not None
        for number, landing in contract.links
    )
    if task is None and has_closed_machine_source:
        print(f"已关闭事项的历史出处保留：{path}")
        return True
    raise GoalError("契约完好，挂出处用 update")


def relink_task_files(path):
    # 修复不能依赖事项正文有效；这里只按开放、关闭目录下的文件名判断归属。
    tasks = {file.stem for file in (path / "tasks").glob("*.md") if file.is_file()}
    done = {file.stem for file in (path / "done").glob("*.md") if file.is_file()}
    return tasks, done


def validate_relink_request(task, sources, quotes, tasks, path):
    if task is None:
        return
    if re.fullmatch(r"T[1-9][0-9]*", task) is None or task not in tasks:
        raise GoalError(f"开放事项 {task} 不存在")
    require_source_quotes(sources, quotes, path)


@dataclass(frozen=True)
class RelinkData:
    path: Path
    text: str
    quotes: dict
    tasks: set
    done: set


def repaired_mappings(data, task, sources):
    links, dismissed, invalid = parse_links(data.text, data.quotes, data.tasks | data.done)
    for line, reason in invalid:
        print(f"丢弃：{line}；{reason}")
    kept_links = set()
    for number, landing in links:
        if landing not in data.tasks:
            # 只修开放事项的机器输入映射；关闭事项的记录已经是历史。
            kept_links.add((number, landing))
            continue
        reason = automatic_reason(data.quotes[number], data.path)
        if reason is None:
            kept_links.add((number, landing))
        else:
            print(f"丢弃：{number} {landing}；原话 {number} 是机器输入（{reason}），不能作为出处")
            dismissed[number] = reason
    links = kept_links
    if task is not None:
        links.update((number, task) for number in sources)
    return links, dismissed


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
    handlers = {
        "add": prepare_add_task,
        "update": prepare_update_task,
        "dismiss": prepare_dismiss_quote,
        "reopen": prepare_reopen_task,
        "accept": prepare_finish_task,
        "cancel": prepare_finish_task,
    }
    changes, removals, added_task = handlers[options.command](contract, options)
    changes[contract.path / "links.md"] = encode_links(contract.links, contract.dismissed)
    write_files(changes, removals)
    if added_task is not None:
        print(added_task)


def prepare_add_task(contract, options):
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
    changes = {contract.path / "tasks" / f"{task}.md": encode_fields(values)}
    return changes, [], task


def prepare_update_task(contract, options):
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
    changes = {}
    if updated_values:
        contract.tasks[task].update(updated_values)
        changes[contract.path / "tasks" / f"{task}.md"] = encode_fields(contract.tasks[task])
    return changes, [], None


def prepare_dismiss_quote(contract, options):
    require_source_quotes([options.quote], contract.quotes, contract.path)
    require_text(options.reason, "原因")
    if any(char in options.reason for char in LINE_BREAKS):
        raise GoalError("原因必须写成一行")
    targets = sorted(target for number, target in contract.links if number == options.quote)
    if targets:
        raise GoalError(f"原话 {options.quote} 已挂事项 " + "、".join(targets))
    contract.dismissed[options.quote] = options.reason
    return {}, [], None


def prepare_reopen_task(contract, options):
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
    # 状态和指针须按重开时的情况重给；旧结论丢弃，出处映射不变。
    require_text(options.state, "状态")
    require_state(options.state)
    require_text(options.pointer, "指针")
    values = {name: contract.done[task][name] for name in ("title", "criteria")}
    values.update(state=options.state, pointer=options.pointer)
    if "evidence" in contract.done[task]:
        values["evidence"] = contract.done[task]["evidence"]
    changes = {contract.path / "tasks" / f"{task}.md": encode_fields(values)}
    removals = [contract.path / "done" / f"{task}.md"]
    return changes, removals, None


def prepare_finish_task(contract, options):
    task = options.task
    require_task(contract, task)
    if options.command == "accept":
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
    # 关闭后状态和指针不再成立；done/ 只留事项、作者证据和结论。
    archived = {name: contract.tasks[task][name] for name in ("title", "criteria")}
    if "evidence" in contract.tasks[task]:
        archived["evidence"] = contract.tasks[task]["evidence"]
    changes = {contract.path / "done" / f"{task}.md": encode_fields({**archived, **conclusion})}
    removals = [contract.path / "tasks" / f"{task}.md"]
    return changes, removals, None


def check(contract, session_id):
    contract = merge_inbox(contract, session_id)
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


def contract_fingerprint(path, goal_content):
    contract = Contract(path, goal_content)
    links_path = path / "links.md"
    links_bytes = links_path.read_bytes()
    link_segments = links_bytes.split(b"\n")
    links_lines = [segment + b"\n" for segment in link_segments[:-1]]
    if link_segments[-1]:
        links_lines.append(link_segments[-1])

    def line_content(line):
        if line.endswith(b"\n"):
            line = line[:-1]
            if line.endswith(b"\r"):
                line = line[:-1]
        return line

    automatic_dismissals = {}
    for number, text in contract.quotes.items():
        reason = automatic_reason(text, path)
        expected_line = f"{number} 不必跟进 {reason}".encode("utf-8") if reason is not None else None
        if (reason is not None and contract.dismissed.get(number) == reason
                and any(line_content(line) == expected_line
                        for line in links_lines)):
            automatic_dismissals[number] = expected_line

    entries = []
    for directory_name in ("quotes", "tasks", "done"):
        directory = path / directory_name
        for entry in sorted(directory.rglob("*"), key=lambda value: value.as_posix()):
            relative = entry.relative_to(path).as_posix()
            # Only omit the numbered quote whose exact automatic dismissal is present.
            if any(relative == f"quotes/{number}.md" for number in automatic_dismissals):
                continue
            if entry.is_symlink():
                entries.append([relative, "symlink", os.readlink(entry)])
            elif entry.is_dir():
                entries.append([relative, "directory"])
            elif entry.is_file():
                entries.append([relative, "file", hashlib.sha256(entry.read_bytes()).hexdigest()])
            else:
                entries.append([relative, "other", entry.lstat().st_mode])
    # The automatic input's exact dismissal row is excluded with its quote; every other
    # mapping remains byte-for-byte in the fingerprint, including its line endings.
    # automatic_reason is the single classifier.
    fingerprint_links = b"".join(
        line for line in links_lines
        if line_content(line) not in automatic_dismissals.values()
    )
    entries.append(["links.md", "file", hashlib.sha256(fingerprint_links).hexdigest()])
    payload = json.dumps({
        "contract_directory": str(path.resolve(strict=True)),
        "target": contract.metadata["target"],
        "entries": entries,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_close_review(contract, report_path):
    report = read_close_review_report(report_path)
    validate_close_review_identity(contract, report)


def read_close_review_report(report_path):
    path = Path(report_path)
    if not path.is_absolute():
        raise GoalError("复核报告路径必须是绝对路径")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise GoalError(f"无法读取复核报告 {path}: {error}") from error

    try:
        report = json.loads(text, object_pairs_hook=unique_json_object)
    except (json.JSONDecodeError, ValueError) as error:
        raise GoalError(f"复核报告 JSON 格式错误：{error}") from error
    required = {"contract_directory", "verdict", "fingerprint", "reviewer_session_id"}
    if not isinstance(report, dict) or set(report) != required:
        raise GoalError("复核报告必须且只能包含 contract_directory、verdict、fingerprint、reviewer_session_id")
    if any(not isinstance(value, str) or not value.strip() for value in report.values()):
        raise GoalError("复核报告字段都必须是非空字符串")
    return report


def unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"字段重复：{key}")
        value[key] = item
    return value


def validate_close_review_identity(contract, report):
    if report["contract_directory"] != str(contract.path.resolve(strict=True)):
        raise GoalError("复核报告的契约目录与当前契约不符")
    if report["verdict"] != "通过":
        raise GoalError("复核结论必须为「通过」")
    current = contract_fingerprint(contract.path, (contract.path / "goal.md").read_bytes())
    if report["fingerprint"] != current:
        raise GoalError("复核报告的契约指纹与当前契约不符")
    reviewer = report["reviewer_session_id"]
    if re.fullmatch(r"[^/\\\n\r\x00]+", reviewer) is None or reviewer in (".", ".."):
        raise GoalError("复核者会话 id 必须是单个目录名")
    # 复核者会话须与当前持有这份契约的会话不同；接管后 session_id 会更新。
    if reviewer == contract.metadata["session_id"]:
        raise GoalError("复核者会话 id 必须与契约当前会话不同")
