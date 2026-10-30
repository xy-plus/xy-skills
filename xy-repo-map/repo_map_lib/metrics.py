"""Count lines and documentation coverage using the suffix-to-language table CODE_SUFFIXES in sibling constants.py."""
from dataclasses import dataclass
import ast
import io
import re
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

    def visit_body(body):
        if body and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                result.update(range(body[0].lineno, body[0].end_lineno + 1))
        for item in body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit_body(item.body)

    visit_body(tree.body)
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
        elif source[index] == "\n" and quote != "`":
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


def _javascript_expression_end(source, start):
    depth = 1
    index = start
    while index < len(source):
        char = source[index]
        if char in ('"', "'"):
            end = _quoted_end(source, index, char)
            index = end if end is not None else index + 1
        elif char == "`":
            end, _ = _template_literal_end(source, index)
            index = end
        elif source.startswith("//", index):
            end = source.find("\n", index)
            index = len(source) if end < 0 else end + 1
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = len(source) if end < 0 else end + 2
        elif char == "{":
            depth += 1
            index += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
            index += 1
        else:
            index += 1
    return None


def _template_literal_end(source, start):
    spans = []
    index = start + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
        elif source[index] == "`":
            return index + 1, spans
        elif source.startswith("${", index):
            expression_start = index + 2
            expression_end = _javascript_expression_end(source, expression_start)
            if expression_end is None:
                return len(source), spans
            for left, right, is_doc in _comment_spans(
                    source[expression_start:expression_end], "TS/JS"):
                spans.append((expression_start + left, expression_start + right, is_doc))
            index = expression_end + 1
        else:
            index += 1
    return len(source), spans


def _comment_spans(source, language):
    spans = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if language == "TS/JS" and char == "`":
            template_end, template_spans = _template_literal_end(source, index)
            spans.extend(template_spans)
            index = template_end
            continue
        if language == "Rust":
            raw_string_end = _rust_raw_string_end(source, index)
            if raw_string_end is not None:
                index = raw_string_end
                continue
        if char in ('"', "`", "'"):
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
                          language in ("C", "TS/JS") and source.startswith("/**", index))
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
    """Classify non-empty code-file lines; inline comments stay code."""
    suffix = Path(path).suffix.lower()
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


def _public_python_coverage(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return (0, 0)
    covered = 0
    total = 0

    def inspect(body):
        nonlocal covered, total
        for item in body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if item.name.startswith("_"):
                    continue
                total += 1
                if ast.get_docstring(item, clean=False) is not None:
                    covered += 1
                if isinstance(item, ast.ClassDef):
                    inspect(item.body)

    inspect(tree.body)
    return covered, total


def _rust_public_coverage(source):
    lines = source.splitlines()
    declaration = re.compile(
        r"^\s*pub\s+(?:(?:async|unsafe|const|extern\s+\"[^\"]+\")\s+)*"
        r"(?:fn|struct|enum|trait|type|const|static|mod|union)\s+[A-Za-z_][A-Za-z0-9_]*"
    )
    covered = 0
    total = 0
    for index, line in enumerate(lines):
        if declaration.search(line) is None:
            continue
        total += 1
        cursor = index - 1
        documented = False
        while cursor >= 0:
            stripped = lines[cursor].strip()
            if stripped.startswith("///") and not stripped.startswith("////"):
                documented = True
                cursor -= 1
                continue
            if stripped.endswith("*/"):
                block = cursor
                while block >= 0:
                    value = lines[block].strip()
                    if value.startswith("/**") or value.startswith("/*!"):
                        documented = True
                        break
                    if not value.startswith("*") and value != "*/":
                        break
                    block -= 1
                break
            break
        covered += int(documented)
    return covered, total


def coverage_counts(path, source):
    """Return (documented public items, public items, language label)."""
    suffix = Path(path).suffix.lower()
    language = CODE_SUFFIXES.get(suffix)
    if language == "Python":
        covered, total = _public_python_coverage(source)
        return covered, total, "Python"
    if language == "Rust":
        covered, total = _rust_public_coverage(source)
        return covered, total, "Rust"
    return 0, 0, "不适用"


def count_file(path, category, content):
    """Count one file according to its link category, ignoring binary data."""
    if isinstance(content, bytes):
        if b"\x00" in content:
            return ZERO_COUNTS
        try:
            source = content.decode("utf-8")
        except UnicodeDecodeError:
            return ZERO_COUNTS
    else:
        source = content
    if category == "code":
        return code_counts(path, source)
    if category == "docs":
        return LineCounts(0, sum(bool(line.strip()) for line in source.splitlines()), 0, 0)
    if category == "tests":
        return LineCounts(0, 0, 0, sum(bool(line.strip()) for line in source.splitlines()))
    if category in ("data", "repo"):
        return ZERO_COUNTS
    raise ValueError(f"未知链接类别：{category}")
