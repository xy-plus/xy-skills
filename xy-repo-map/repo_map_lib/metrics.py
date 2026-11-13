from collections import namedtuple
import ast
import io
import tokenize
from pathlib import Path

from .constants import CODE_SUFFIXES


LineCounts = namedtuple("LineCounts", "code docs comments tests")


def count_non_whitespace_characters(text):
    """Count the characters in text that are not whitespace."""
    return sum(not character.isspace() for character in text)


def _python_doc_lines(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ast.get_docstring(node, clean=False) is not None:
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
                end = _quoted_end(source, index, char, allow_newlines=language == "Rust")
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


def _span_line_categories(source, spans):
    offset = 0
    for number, line in enumerate(source.splitlines(keepends=True), 1):
        content = line.rstrip("\r\n")
        if content.strip():
            visible = [
                offset + index for index, char in enumerate(content)
                if not char.isspace()
            ]
            covering = [
                span for span in spans
                if span[0] < offset + len(content) and span[1] > offset
            ]
            covered_positions = [
                position for position in visible
                if any(start <= position < end for start, end, _ in covering)
            ]
            if visible and len(covered_positions) == len(visible):
                category = (
                    "docs" if any(is_doc for _, _, is_doc in covering)
                    else "comments"
                )
            else:
                category = "code"
            yield number, category
        offset += len(line)


def _span_counts(source, spans):
    line_counts = {"code": 0, "docs": 0, "comments": 0}
    for _number, category in _span_line_categories(source, spans):
        line_counts[category] += 1
    return LineCounts(line_counts["code"], line_counts["docs"], line_counts["comments"], 0)


def code_counts(path, source):
    suffix = Path(path).suffix.lower()
    if suffix == ".html":
        return _html_counts(source)
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
    return _span_counts(source, _comment_spans(source, language))


def _html_counts(source):
    spans = []
    start = 0
    while True:
        start = source.find("<!--", start)
        if start < 0:
            break
        end = source.find("-->", start + 4)
        if end < 0:
            spans.append((start, len(source), False))
            break
        spans.append((start, end + 3, False))
        start = end + 3
    return _span_counts(source, spans)


def count_file(path, category, content):
    if category == "code":
        return code_counts(path, content)
    if category == "docs":
        return LineCounts(0, sum(bool(line.strip()) for line in content.splitlines()), 0, 0)
    if category == "tests":
        return LineCounts(0, 0, 0, sum(bool(line.strip()) for line in content.splitlines()))
    if category == "other":
        return LineCounts(0, 0, 0, 0)


def count_doc_chars(path, category, content):
    """Count non-whitespace characters on lines classified as documentation."""
    if category == "docs":
        documentation_lines = {
            number for number, line in enumerate(content.splitlines(), 1)
            if line.strip()
        }
    elif category == "code":
        suffix = Path(path).suffix.lower()
        if suffix == ".html":
            return 0
        language = CODE_SUFFIXES[suffix]
        if language == "Python":
            documentation_lines = _python_doc_lines(content)
        elif language == "Shell":
            return 0
        else:
            spans = _comment_spans(content, language)
            documentation_lines = {
                number for number, category_name
                in _span_line_categories(content, spans)
                if category_name == "docs"
            }
    else:
        return 0

    return sum(
        count_non_whitespace_characters(line)
        for number, line in enumerate(content.splitlines(), 1)
        if number in documentation_lines
    )
