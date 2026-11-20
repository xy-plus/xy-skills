from collections import Counter, namedtuple
import ast
import io
from pathlib import Path
import sys
import tokenize

from ..constants import CODE_SUFFIXES

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from text_metrics import count_non_whitespace_characters


LineCounts = namedtuple("LineCounts", "code docs comments tests doc_chars")


def _python_doc_lines(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    result = set()
    for node in ast.walk(tree):
        if (
            isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
            and ast.get_docstring(node, clean=False) is not None
        ):
            doc = node.body[0]
            result.update(range(doc.lineno, doc.end_lineno + 1))
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
            if before.strip() == "" and not (
                line_number == 1 and token.string.startswith("#!")
            ):
                result.add(line_number)
    except (tokenize.TokenError, IndentationError):
        pass
    return result


def _quoted_end(source, start, quote, allow_newlines):
    index = start + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
        elif source[index] == quote:
            return index + 1
        elif source[index] == "\n" and not allow_newlines:
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
                end = _quoted_end(source, index, char, allow_newlines=False)
                if end is not None and end - index <= 6:
                    index = end
                    continue
            else:
                end = _quoted_end(
                    source, index, char, allow_newlines=language == "Rust"
                )
                if end is not None:
                    index = end
                    continue
        if source.startswith("//", index):
            end = source.find("\n", index)
            if end < 0:
                end = length
            marker = source[index:end]
            if language == "Rust":
                is_doc = (
                    marker.startswith("///") and not marker.startswith("////")
                ) or marker.startswith("//!")
            elif language == "C":
                is_doc = marker.startswith("///")
            else:
                is_doc = False
            spans.append((index, end, is_doc))
            index = end
            continue
        if source.startswith("/*", index):
            is_doc = (
                language == "Rust"
                and (source.startswith("/**", index) or source.startswith("/*!", index))
            ) or (language == "C" and source.startswith("/**", index))
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


def _span_line_categories(source, spans):
    offset = 0
    for number, line in enumerate(source.splitlines(keepends=True), 1):
        content = line.rstrip("\r\n")
        if content.strip():
            visible = [
                offset + index
                for index, char in enumerate(content)
                if not char.isspace()
            ]
            covering = [
                span
                for span in spans
                if span[0] < offset + len(content) and span[1] > offset
            ]
            if visible and all(
                any(start <= position < end for start, end, _ in covering)
                for position in visible
            ):
                if any(is_doc for _, _, is_doc in covering):
                    yield number, "docs"
                else:
                    yield number, "comments"
            else:
                yield number, "code"
        offset += len(line)


def _documentation_characters(source, line_numbers):
    return sum(
        count_non_whitespace_characters(line)
        for number, line in enumerate(source.splitlines(), 1)
        if number in line_numbers
    )


def _line_counts(categories, doc_chars=0):
    counts = Counter(category for _number, category in categories)
    return LineCounts(
        counts["code"],
        counts["docs"],
        counts["comments"],
        counts["tests"],
        doc_chars,
    )


def _span_metrics(source, spans):
    categories = list(_span_line_categories(source, spans))
    doc_lines = {number for number, category in categories if category == "docs"}
    return _line_counts(categories, _documentation_characters(source, doc_lines))


def _html_comment_spans(source):
    spans = []
    start = 0
    while True:
        start = source.find("<!--", start)
        if start < 0:
            return spans
        end = source.find("-->", start + 4)
        if end < 0:
            spans.append((start, len(source), False))
            return spans
        spans.append((start, end + 3, False))
        start = end + 3


def _python_metrics(source):
    doc_lines = _python_doc_lines(source)
    comment_lines = _python_comment_lines(source)
    categories = [
        (
            number,
            "docs"
            if number in doc_lines
            else "comments"
            if number in comment_lines
            else "code",
        )
        for number, line in enumerate(source.splitlines(), 1)
        if line.strip()
    ]
    return _line_counts(categories, _documentation_characters(source, doc_lines))


def _shell_metrics(source):
    categories = []
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip()
        if not stripped:
            continue
        category = (
            "comments"
            if stripped.startswith("#")
            and not (number == 1 and stripped.startswith("#!"))
            else "code"
        )
        categories.append((number, category))
    return _line_counts(categories)


def _code_metrics(path, source):
    suffix = Path(path).suffix.lower()
    if suffix == ".html":
        return _span_metrics(source, _html_comment_spans(source))
    language = CODE_SUFFIXES[suffix]
    if language == "Python":
        return _python_metrics(source)
    if language == "Shell":
        return _shell_metrics(source)
    return _span_metrics(source, _comment_spans(source, language))


def count_file(path, category, content):
    """Count nonblank lines and documentation characters in one source scan."""
    if category == "code":
        return _code_metrics(path, content)
    if category == "docs":
        documentation_lines = {
            number
            for number, line in enumerate(content.splitlines(), 1)
            if line.strip()
        }
        return _line_counts(
            [(number, "docs") for number in documentation_lines],
            _documentation_characters(content, documentation_lines),
        )
    if category == "tests":
        lines = [line for line in content.splitlines() if line.strip()]
        return LineCounts(0, 0, 0, len(lines), 0)
    return LineCounts(0, 0, 0, 0, 0)
