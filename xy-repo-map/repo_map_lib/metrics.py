"""Count code, documentation, comment, and test lines for tracked files."""
from dataclasses import dataclass
import ast
import io
import tokenize
from pathlib import Path

from .constants import CODE_SUFFIXES


@dataclass(frozen=True)
class LineCounts:
    code: int
    docs: int
    comments: int
    tests: int


ZERO_COUNTS = LineCounts(0, 0, 0, 0)


def _python_doc_lines(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    result = set()

    def add_docstring(body):
        if body and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                result.update(range(body[0].lineno, body[0].end_lineno + 1))

    def visit_definitions(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add_docstring(node.body)
        for child in ast.iter_child_nodes(node):
            visit_definitions(child)

    add_docstring(tree.body)
    visit_definitions(tree)
    return result


def _python_comment_lines(source):
    result = set()
    lines = source.splitlines()
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            line_number, column = token.start
            before = lines[line_number - 1][:column]
            if before.strip() == "" and not (line_number == 1 and token.string.startswith("#!")):
                result.add(line_number)
    except (tokenize.TokenError, IndentationError):
        pass
    return result


def _python_counts(source):
    docs = _python_doc_lines(source)
    comments = _python_comment_lines(source)
    code = 0
    doc_count = 0
    comment_count = 0
    for number, line in enumerate(source.splitlines(), 1):
        if not line.strip():
            continue
        if number in docs:
            doc_count += 1
        elif number in comments:
            comment_count += 1
        else:
            code += 1
    return LineCounts(code, doc_count, comment_count, 0)


def _quoted_end(source, start, quote):
    index = start + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
        elif source[index] == quote:
            return index + 1
        elif source[index] == "\n":
            return None
        else:
            index += 1
    return None


def _rust_raw_string_end(source, start):
    if start and (source[start - 1].isalnum() or source[start - 1] == "_"):
        return None
    if source.startswith("br", start):
        prefix_length = 2
        index = start + 2
    elif source.startswith("r", start):
        prefix_length = 1
        index = start + 1
    else:
        return None
    while index < len(source) and source[index] == "#":
        index += 1
    if index >= len(source) or source[index] != '"':
        return None
    delimiter = '"' + "#" * (index - start - prefix_length)
    ending = source.find(delimiter, index + 1)
    return len(source) if ending < 0 else ending + len(delimiter)


def _comment_spans(source, language):
    spans = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if language == "Rust":
            raw_string_end = _rust_raw_string_end(source, index)
            if raw_string_end is not None:
                index = raw_string_end
                continue
        if char in ('"', "'"):
            if language == "Rust" and char == "'":
                # Rust lifetimes are not quoted strings; only skip character literals.
                end = _quoted_end(source, index, char)
                if end is not None and end - index <= 6:
                    index = end
                    continue
            else:
                end = _quoted_end(source, index, char)
                if end is not None:
                    index = end
                    continue
        if source.startswith("//", index):
            end = source.find("\n", index)
            if end < 0:
                end = length
            marker = source[index:end]
            if language == "Rust":
                is_doc = (marker.startswith("///") and not marker.startswith("////")) or marker.startswith("//!")
            elif language == "C":
                is_doc = marker.startswith("///")
            else:
                is_doc = False
            spans.append((index, end, is_doc))
            index = end
            continue
        if source.startswith("/*", index):
            is_doc = (language == "Rust" and
                      (source.startswith("/**", index) or source.startswith("/*!", index))) or (
                          language == "C" and source.startswith("/**", index))
            cursor = index + 2
            depth = 1
            while cursor < length and depth:
                if language == "Rust" and source.startswith("/*", cursor):
                    depth += 1
                    cursor += 2
                elif source.startswith("*/", cursor):
                    depth -= 1
                    cursor += 2
                else:
                    cursor += 1
            spans.append((index, cursor, is_doc))
            index = cursor
            continue
        index += 1
    return spans


def _lexed_counts(source, language):
    spans = _comment_spans(source, language)
    line_counts = {"code": 0, "docs": 0, "comments": 0}
    offset = 0
    for line in source.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if content.strip():
            visible = [offset + index for index, char in enumerate(content) if not char.isspace()]
            covering = [span for span in spans if span[0] < offset + len(content) and span[1] > offset]
            covered_positions = [
                position for position in visible
                if any(start <= position < end for start, end, _ in covering)
            ]
            if visible and len(covered_positions) == len(visible):
                line_counts["docs" if any(is_doc for _, _, is_doc in covering) else "comments"] += 1
            else:
                line_counts["code"] += 1
        offset += len(line)
    return LineCounts(line_counts["code"], line_counts["docs"], line_counts["comments"], 0)


def code_counts(path, source):
    """Classify non-empty source lines; inline comments stay code."""
    suffix = Path(path).suffix.lower()
    if suffix == ".css":
        return _lexed_counts(source, "C")
    if suffix == ".html":
        return _html_counts(source)
    if suffix not in CODE_SUFFIXES:
        raise ValueError(f"code 文件后缀不支持：{suffix or '<无后缀>'}")
    language = CODE_SUFFIXES[suffix]
    if language == "Python":
        return _python_counts(source)
    if language == "Shell":
        code = 0
        comments = 0
        for number, line in enumerate(source.splitlines(), 1):
            stripped = line.lstrip()
            if not stripped:
                continue
            if stripped.startswith("#") and not (number == 1 and stripped.startswith("#!")):
                comments += 1
            else:
                code += 1
        return LineCounts(code, 0, comments, 0)
    return _lexed_counts(source, language)


def _html_counts(source):
    spans = []
    start = 0
    while True:
        start = source.find("<!--", start)
        if start < 0:
            break
        end = source.find("-->", start + 4)
        if end < 0:
            spans.append((start, len(source)))
            break
        spans.append((start, end + 3))
        start = end + 3

    counts = {"code": 0, "comments": 0}
    offset = 0
    for line in source.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        visible = [offset + index for index, char in enumerate(content) if not char.isspace()]
        if visible:
            if all(any(begin <= position < end for begin, end in spans) for position in visible):
                counts["comments"] += 1
            else:
                counts["code"] += 1
        offset += len(line)
    return LineCounts(counts["code"], 0, counts["comments"], 0)


def count_file(path, category, content):
    """Count one file from decoded text and its file category."""
    if not isinstance(content, str):
        raise TypeError("文件内容必须是文本")
    if category == "code":
        return code_counts(path, content)
    if category == "docs":
        return LineCounts(0, sum(bool(line.strip()) for line in content.splitlines()), 0, 0)
    if category == "tests":
        return LineCounts(0, 0, 0, sum(bool(line.strip()) for line in content.splitlines()))
    if category == "other":
        return ZERO_COUNTS
    raise ValueError(f"未知文件类别：{category}")
