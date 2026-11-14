"""List and verify comment-only edits while preserving source outside comments."""
import argparse
import ast
import bisect
import json
import os
from pathlib import Path
import re
import sys

from pygments import lexers
from pygments.lexers import ClassNotFound
from pygments.token import Comment, String


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "xy-handoff"))
from check_handoff import object_without_duplicate_keys  # noqa: E402


EXCLUDED_COMMENTS = (Comment.Preproc, Comment.PreprocFile, Comment.Hashbang)
TOOL_MARKERS = (
    "noqa", "type:", "pragma", "pylint:", "fmt:", "isort:", "shellcheck",
    "NOLINT", "clang-format", "prettier-ignore",
)
JUDGEMENT_FIELDS = {"inventory", "added"}
INVENTORY_FIELDS = {
    "id", "block", "lines", "original", "status", "rewritten", "impact", "why"
}
ADDED_FIELDS = {"text", "why"}
FENCE = re.compile(r"\x60\x60\x60.*?\x60\x60\x60", re.DOTALL)
DOCTEST_LINE = re.compile(r"(?m)^[ \t]*(?:>>> |\.\.\. )[^\r\n]*(?:\r\n|\r|\n|$)")


class RewriteFormatError(Exception):
    """A block list or judgement does not follow its documented shape."""


def parser():
    result = argparse.ArgumentParser(description="核对代码注释改写")
    commands = result.add_subparsers(dest="command", required=True)

    blocks = commands.add_parser("blocks", help="列出原文的注释块")
    blocks.add_argument("--original", required=True, help="原文件路径")

    splice = commands.add_parser("splice", help="将块清单拼回原文")
    splice.add_argument("--original", required=True, help="未改写的原文件")
    splice.add_argument("--blocks", required=True, help="块清单 JSON")
    splice.add_argument("--output", required=True, help="改写稿路径")

    gate = commands.add_parser("gate", help="核对独立评审结果")
    gate.add_argument("--original", required=True, help="未改写的原文件")
    gate.add_argument("--rewrite", required=True, help="改写稿")
    gate.add_argument("--blocks", required=True, help="拼接改写稿所用的块清单 JSON")
    gate.add_argument("--judgement", required=True, help="评审 JSON")

    return result


def _read_source(path):
    return Path(path).read_bytes().decode("utf-8")


def _lexer(path):
    try:
        return lexers.get_lexer_for_filename(str(path))
    except ClassNotFound as error:
        raise ValueError(f"不支持的语言：{path}") from error


def _annotation_type(token):
    if token in String.Doc:
        return "doc"
    if token in Comment and not any(token in excluded for excluded in EXCLUDED_COMMENTS):
        return "comment"
    return None


def _tokenize(source, path):
    return list(_lexer(path).get_tokens_unprocessed(source))


def _line_starts(source):
    return [0] + [match.end() for match in re.finditer(r"\r\n|\r|\n", source)]


def _line_number(offset, starts):
    return bisect.bisect_right(starts, offset)


def _comment_block_spans(source, tokens):
    groups = []
    for start, token, value in tokens:
        kind = _annotation_type(token)
        if kind is None:
            continue
        end = start + len(value)
        while end > start and source[end - 1] in "\r\n":
            end -= 1
        gap = source[groups[-1][1]:start] if groups else None
        if (kind == "comment" and groups and groups[-1][2] == "comment"
                and (not gap or gap.isspace())):
            groups[-1][1] = end
        else:
            groups.append([start, end, kind])

    starts = _line_starts(source)
    return [
        {
            "id": str(index),
            "lines": f"{_line_number(start, starts)}-{_line_number(end, starts)}",
            "text": source[start:end],
            "start": start,
            "end": end,
        }
        for index, (start, end, _) in enumerate(groups, start=1)
    ]


def _blocks_command(arguments):
    source = _read_source(arguments.original)
    tokens = _tokenize(source, arguments.original)
    blocks = [
        {key: block[key] for key in ("id", "lines", "text")}
        for block in _comment_block_spans(source, tokens)
    ]
    print(json.dumps(blocks, ensure_ascii=False, indent=2))
    return 0


def _same_file(first, second):
    first_path = Path(first)
    second_path = Path(second)
    if first_path.resolve() == second_path.resolve():
        return True
    try:
        return os.path.samefile(first_path, second_path)
    except OSError:
        return False


def _full_line_span(source, start, end):
    starts = _line_starts(source)
    first_line = bisect.bisect_right(starts, start) - 1
    last_line = bisect.bisect_right(starts, end) - 1
    line_start = starts[first_line]
    line_end = starts[last_line + 1] if last_line + 1 < len(starts) else len(source)
    if source[line_start:start].strip() or source[end:line_end].strip():
        return start, end
    return line_start, line_end


def _parse_json_file(path, label):
    try:
        return json.loads(
            _read_source(path), object_pairs_hook=object_without_duplicate_keys
        )
    except (OSError, UnicodeError) as error:
        raise OSError(f"无法读取{label}：{path}：{error}") from error
    except (json.JSONDecodeError, ValueError) as error:
        raise RewriteFormatError(f"{label}格式错误：{error}") from error


def _prepare_edits(source, tokens, manifest):
    if not isinstance(manifest, list):
        raise RewriteFormatError("块清单必须是数组")
    spans = _comment_block_spans(source, tokens)
    by_id = {block["id"]: block for block in spans}
    seen = set()
    edits = []
    for index, item in enumerate(manifest):
        where = f"块清单第 {index + 1} 项"
        if not isinstance(item, dict):
            raise RewriteFormatError(f"{where}必须是对象")
        if item.keys() != {"id", "text"}:
            raise RewriteFormatError(f"{where}必须恰有 id、text 两键")
        block_id = item["id"]
        replacement = item["text"]
        if not isinstance(block_id, str) or not block_id:
            raise RewriteFormatError(f"{where}.id 必须是非空字符串")
        if block_id not in by_id:
            raise RewriteFormatError(f"{where}.id 不属于 blocks 输出：{block_id}")
        if block_id in seen:
            raise RewriteFormatError(f"块 id 重复：{block_id}")
        if not isinstance(replacement, str):
            raise RewriteFormatError(f"{where}.text 必须是字符串")
        seen.add(block_id)
        block = by_id[block_id]
        start, end = block["start"], block["end"]
        if replacement == "":
            start, end = _full_line_span(source, start, end)
        edits.append({"start": start, "end": end, "text": replacement})

    edits.sort(key=lambda edit: edit["start"])
    previous_end = -1
    for edit in edits:
        if edit["start"] < previous_end:
            raise RewriteFormatError("块清单的替换范围重叠")
        previous_end = edit["end"]
    return edits


def _splice_text(source, edits):
    parts = []
    insertions = []
    old_cursor = 0
    new_cursor = 0
    for edit in edits:
        unchanged = source[old_cursor:edit["start"]]
        parts.append(unchanged)
        new_cursor += len(unchanged)
        replacement_start = new_cursor
        parts.append(edit["text"])
        new_cursor += len(edit["text"])
        if edit["text"]:
            insertions.append((replacement_start, new_cursor))
        old_cursor = edit["end"]
    parts.append(source[old_cursor:])
    return "".join(parts), insertions


def _insertions_are_annotations(source, tokens, insertions):
    problems = []
    for start, end in insertions:
        for token_start, token, value in tokens:
            token_end = token_start + len(value)
            overlap_start = max(start, token_start)
            overlap_end = min(end, token_end)
            if overlap_start >= overlap_end:
                continue
            if (_annotation_type(token) is None
                    and source[overlap_start:overlap_end].strip()):
                problems.append("替换文字切出了注释，包含非空白代码")
                break
    return problems


def _annotation_texts(tokens, kind=None):
    result = []
    for _, token, value in tokens:
        annotation = _annotation_type(token)
        if annotation is not None and (kind is None or annotation == kind):
            result.append(value)
    return result


def _ordered_texts_present(required, available):
    text = "".join(available)
    cursor = 0
    for snippet in required:
        position = text.find(snippet, cursor)
        if position < 0:
            return False
        cursor = position + len(snippet)
    return True


def _examples(text):
    fenced = list(FENCE.finditer(text))
    examples = [(match.start(), match.group()) for match in fenced]
    for match in DOCTEST_LINE.finditer(text):
        if not any(fence.start() <= match.start() < fence.end() for fence in fenced):
            examples.append((match.start(), match.group()))
    return [value for _, value in sorted(examples)]


def _without_python_docstrings(tree):
    containers = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, containers) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    return tree


def _splice_problems(original, original_tokens, rewritten, rewritten_tokens, path,
                     insertions):
    problems = _insertions_are_annotations(rewritten, rewritten_tokens, insertions)
    required_tools = [
        value.rstrip("\r\n")
        for value in _annotation_texts(original_tokens, "comment")
        if any(marker in value for marker in TOOL_MARKERS)
    ]
    actual_comments = _annotation_texts(rewritten_tokens, "comment")
    if not _ordered_texts_present(required_tools, actual_comments):
        problems.append("工具注释被改动或删除")

    original_annotations = _annotation_texts(original_tokens)
    rewritten_annotations = _annotation_texts(rewritten_tokens)
    required_examples = _examples("".join(original_annotations))
    if not _ordered_texts_present(required_examples, rewritten_annotations):
        problems.append("注释里的代码示例或 doctest 行被改动或删除")

    if Path(path).suffix.lower() == ".py":
        try:
            original_tree = _without_python_docstrings(ast.parse(original))
            rewritten_tree = _without_python_docstrings(ast.parse(rewritten))
        except SyntaxError as error:
            problems.append(f"Python 文档串核对失败：{error}")
        else:
            if ast.dump(original_tree) != ast.dump(rewritten_tree):
                problems.append("Python 文档串以外的 AST 发生变化")
    return problems


def _splice_candidate(source, path, manifest, original_tokens):
    edits = _prepare_edits(source, original_tokens, manifest)
    rewritten, insertions = _splice_text(source, edits)
    rewritten_tokens = _tokenize(rewritten, path)
    problems = _splice_problems(
        source, original_tokens, rewritten, rewritten_tokens, path, insertions
    )
    return rewritten, rewritten_tokens, problems


def _splice_command(arguments):
    if _same_file(arguments.original, arguments.output):
        print("--output 和 --original 是同一路径或同一文件")
        return 2
    source = _read_source(arguments.original)
    manifest = _parse_json_file(arguments.blocks, "块清单")
    original_tokens = _tokenize(source, arguments.original)
    rewritten, _, problems = _splice_candidate(
        source, arguments.original, manifest, original_tokens
    )
    if problems:
        print("\n".join(problems))
        return 1
    try:
        Path(arguments.output).write_bytes(rewritten.encode("utf-8"))
    except OSError as error:
        raise OSError(f"无法写入改写稿：{arguments.output}：{error}") from error
    return 0


def _parse_line_range(value, where, problems):
    if not isinstance(value, str):
        problems.append(f"{where}.lines 必须是起始行-结束行字符串")
        return None
    match = re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)", value)
    if match is None:
        problems.append(f"{where}.lines 格式错误：{value!r}")
        return None
    start, end = map(int, match.groups())
    if start > end:
        problems.append(f"{where}.lines 起始行大于结束行：{value}")
        return None
    return start, end


def _require_exact_keys(value, expected, where, problems):
    if not isinstance(value, dict):
        problems.append(f"{where}必须是对象")
        return False
    for key in sorted(expected - value.keys()):
        problems.append(f"{where}缺少字段：{key}")
    for key in sorted(value.keys() - expected):
        problems.append(f"{where}有多余字段：{key}")
    return True


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _text_occurs_in_line_range(block, text, line_range):
    block_start = int(block["lines"].split("-", 1)[0])
    line_starts = _line_starts(block["text"])
    cursor = 0
    while True:
        position = block["text"].find(text, cursor)
        if position < 0:
            return False
        first_line = block_start + _line_number(position, line_starts) - 1
        last_offset = position + len(text) - 1
        last_line = block_start + _line_number(last_offset, line_starts) - 1
        if line_range[0] <= first_line and last_line <= line_range[1]:
            return True
        cursor = position + 1


def _check_judgement(judgement, original_blocks, rewrite_annotation_blocks):
    problems = []
    if not _require_exact_keys(judgement, JUDGEMENT_FIELDS, "评审 JSON", problems):
        return problems
    inventory = judgement.get("inventory")
    added = judgement.get("added")
    if not isinstance(inventory, list):
        problems.append("评审 JSON.inventory 必须是数组")
        inventory = []
    if not isinstance(added, list):
        problems.append("评审 JSON.added 必须是数组")
        added = []

    blocks_by_id = {block["id"]: block for block in original_blocks}
    inventory_ids = set()
    covered = {block["id"]: set() for block in original_blocks}
    for index, item in enumerate(inventory):
        where = f"inventory[{index}]"
        if not _require_exact_keys(item, INVENTORY_FIELDS, where, problems):
            continue

        item_id = item.get("id")
        if not _nonempty_string(item_id):
            problems.append(f"{where}.id 必须是非空字符串")
        elif item_id in inventory_ids:
            problems.append(f"inventory id 重复：{item_id}")
        else:
            inventory_ids.add(item_id)

        block_id = item.get("block")
        block = blocks_by_id.get(block_id) if isinstance(block_id, str) else None
        if block is None:
            problems.append(f"{where}.block 不属于原文 blocks：{block_id!r}")

        item_range = _parse_line_range(item.get("lines"), where, problems)
        block_range = (
            _parse_line_range(block["lines"], f"原文 block {block_id}", problems)
            if block is not None else None
        )
        if item_range is not None and block_range is not None:
            start, end = item_range
            block_start, block_end = block_range
            if start < block_start or end > block_end:
                problems.append(
                    f"{where}.lines {item['lines']} 超出 block {block_id} 的行号范围 "
                    f"{block['lines']}"
                )
            elif block_id in covered:
                covered[block_id].update(range(start, end + 1))

        original_text = item.get("original")
        if not _nonempty_string(original_text):
            problems.append(f"{where}.original 必须是非空字符串")
        elif block is not None:
            if original_text not in block["text"]:
                problems.append(f"{where}.original 找不到于 block {block_id} 原文")
            elif (item_range is not None
                    and not _text_occurs_in_line_range(block, original_text, item_range)):
                problems.append(
                    f"{where}.original 不在 lines {item['lines']} 指定的原文行内"
                )

        status = item.get("status")
        if status not in ("在", "变了", "丢了"):
            problems.append(f"{where}.status 只能是 在、变了、丢了")
        rewritten = item.get("rewritten")
        if not isinstance(rewritten, str):
            problems.append(f"{where}.rewritten 必须是字符串")
        elif status == "丢了":
            if rewritten != "":
                problems.append(f"{where}.status 为丢了时 rewritten 必须为空")
        elif status in ("在", "变了"):
            if not rewritten.strip():
                problems.append(f"{where}.status 为{status}时 rewritten 不能为空")
            elif not any(rewritten in block for block in rewrite_annotation_blocks):
                problems.append(f"{where}.rewritten 找不到于改写稿注释")

        impact = item.get("impact")
        if not isinstance(impact, str):
            problems.append(f"{where}.impact 必须是字符串")
        elif status == "在":
            if impact != "":
                problems.append(f"{where}.status 为在时 impact 必须为空")
        elif status in ("变了", "丢了"):
            if impact not in ("有影响", "没影响"):
                problems.append(f"{where}.impact 只能是 有影响、没影响")
            elif impact == "有影响":
                problems.append(f"{where}有影响的信息不能变了或丢了")

        why = item.get("why")
        if not isinstance(why, str):
            problems.append(f"{where}.why 必须是字符串")
        elif status != "在" and not why.strip():
            problems.append(f"{where}.status 为{status}时 why 不能为空")

    for block in original_blocks:
        start, _ = map(int, block["lines"].split("-", 1))
        for offset, line in enumerate(block["text"].splitlines(), start=start):
            if line.strip() and offset not in covered[block["id"]]:
                problems.append(
                    f"原文 block {block['id']} 第 {offset} 行没有评审条目覆盖"
                )

    for index, item in enumerate(added):
        where = f"added[{index}]"
        if not _require_exact_keys(item, ADDED_FIELDS, where, problems):
            continue
        for field in ("text", "why"):
            if not _nonempty_string(item.get(field)):
                problems.append(f"{where}.{field} 必须是非空字符串")
    if added:
        problems.append(f"added 非空：{len(added)} 条")
    return problems


def _gate_command(arguments):
    original = _read_source(arguments.original)
    rewrite_bytes = Path(arguments.rewrite).read_bytes()
    rewritten = rewrite_bytes.decode("utf-8")
    manifest = _parse_json_file(arguments.blocks, "块清单")
    judgement = _parse_json_file(arguments.judgement, "评审 JSON")
    original_tokens = _tokenize(original, arguments.original)
    expected_rewrite, rewrite_tokens, splice_problems = _splice_candidate(
        original, arguments.original, manifest, original_tokens
    )
    original_blocks = _comment_block_spans(original, original_tokens)
    if splice_problems:
        print("\n".join(splice_problems))
        return 1
    if rewrite_bytes != expected_rewrite.encode("utf-8"):
        print("改写稿字节与按块清单拼接结果不同")
        return 1
    rewrite_annotation_blocks = [
        block["text"]
        for block in _comment_block_spans(rewritten, rewrite_tokens)
    ]
    problems = _check_judgement(
        judgement, original_blocks, rewrite_annotation_blocks
    )
    if problems:
        print("\n".join(problems))
        return 1
    return 0


def main(argv=None):
    arguments = parser().parse_args(argv)
    commands = {
        "blocks": _blocks_command,
        "splice": _splice_command,
        "gate": _gate_command,
    }
    try:
        return commands[arguments.command](arguments)
    except (OSError, UnicodeError, ValueError) as error:
        print(error)
        return 2
    except RewriteFormatError as error:
        print(error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
