"""Extract stable per-file and per-definition identifiers for cleanup audits."""
import ast
from collections import Counter
from pathlib import Path


def _python_definitions(source, file_path):
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as error:
        raise ValueError(
            f"无法解析 Python 实体 {file_path}:{error.lineno}: {error.msg}"
        ) from error
    definitions = []
    function_types = (ast.FunctionDef, ast.AsyncFunctionDef)

    def visit_nested(node, qualifier):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (*function_types, ast.ClassDef)):
                visit_definition(child, qualifier)
            else:
                visit_nested(child, qualifier)

    def visit_statement(statement, qualifier):
        if isinstance(statement, (*function_types, ast.ClassDef)):
            visit_definition(statement, qualifier)
        else:
            visit_nested(statement, qualifier)

    def visit_definition(item, qualifier):
        name = f"{qualifier}.{item.name}" if qualifier else item.name
        definitions.append((f"{file_path}::{name}", item.lineno))
        for statement in item.body:
            visit_statement(statement, name)

    for item in tree.body:
        visit_statement(item, "")
    return definitions


def _raw_string_end(source, start):
    prefix_length = 0
    for prefix in ("br", "cr", "r"):
        if source.startswith(prefix, start):
            prefix_length = len(prefix)
            break
    if not prefix_length:
        return None
    index = start + prefix_length
    while index < len(source) and source[index] == "#":
        index += 1
    if index >= len(source) or source[index] != '"':
        return None
    closing = '"' + "#" * (index - start - prefix_length)
    end = source.find(closing, index + 1)
    return len(source) if end < 0 else end + len(closing)


def _quoted_string_end(source, quote_index):
    index = quote_index + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
        elif source[index] == '"':
            return index + 1
        else:
            index += 1
    return len(source)


def _char_literal_end(source, quote_index):
    index = quote_index + 1
    if index >= len(source) or source[index] == "\n":
        return None
    if source[index] == "\\":
        index += 1
        if index >= len(source):
            return None
        if source[index] == "u" and index + 1 < len(source) and source[index + 1] == "{":
            closing = source.find("}", index + 2)
            if closing < 0:
                return None
            index = closing + 1
        else:
            index += 1
    else:
        index += 1
    if index < len(source) and source[index] == "'":
        return index + 1
    return None


def _identifier_end(source, start):
    index = start + 1
    while index < len(source) and (source[index] == "_" or source[index].isalnum()):
        index += 1
    return index


def _rust_tokens(source):
    tokens = []
    index = 0
    line = 1
    while index < len(source):
        character = source[index]
        if character == "\n":
            line += 1
            index += 1
            continue
        if character.isspace():
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline
            continue
        if source.startswith("/*", index):
            index += 2
            depth = 1
            while index < len(source) and depth:
                if source.startswith("/*", index):
                    depth += 1
                    index += 2
                elif source.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    if source[index] == "\n":
                        line += 1
                    index += 1
            continue

        raw_end = _raw_string_end(source, index)
        if raw_end is not None:
            line += source.count("\n", index, raw_end)
            index = raw_end
            continue
        if character in {"b", "c"} and index + 1 < len(source) and source[index + 1] == '"':
            end = _quoted_string_end(source, index + 1)
            line += source.count("\n", index, end)
            index = end
            continue
        if character == '"':
            end = _quoted_string_end(source, index)
            line += source.count("\n", index, end)
            index = end
            continue
        if character == "'":
            end = _char_literal_end(source, index)
            if end is not None:
                line += source.count("\n", index, end)
                index = end
            else:
                index += 1
            continue

        if source.startswith("r#", index) and index + 2 < len(source):
            name_start = index + 2
            if source[name_start] == "_" or source[name_start].isalpha():
                end = _identifier_end(source, name_start)
                tokens.append((source[name_start:end], line, True))
                index = end
                continue
        if character == "_" or character.isalpha():
            end = _identifier_end(source, index)
            tokens.append((source[index:end], line, True))
            index = end
            continue
        tokens.append((character, line, False))
        index += 1
    return tokens


def _rust_definitions(source, file_path):
    tokens = _rust_tokens(source)
    return [
        (f"{file_path}::{tokens[index + 1][0]}", line)
        for index, (token, line, _) in enumerate(tokens[:-1])
        if token == "fn" and tokens[index + 1][2]
    ]


def _with_duplicate_lines(definitions):
    counts = Counter(entity_id for entity_id, _ in definitions)
    entities = [
        f"{entity_id}@{line}" if counts[entity_id] > 1 else entity_id
        for entity_id, line in definitions
    ]
    if len(entities) != len(set(entities)):
        raise ValueError("同一行有无法区分的重名实体")
    return entities


def file_entities(repository, file_path):
    """Return one tracked-file ID plus definitions applicable to its source type."""
    root = Path(repository)
    entities = [file_path]
    suffix = Path(file_path).suffix
    if suffix == ".py":
        try:
            source = (root / file_path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            raise ValueError(f"无法读取 Python 实体 {file_path}：{error}") from error
        definitions = _python_definitions(source, file_path)
    elif suffix == ".rs":
        try:
            source = (root / file_path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            raise ValueError(f"无法读取 Rust 实体 {file_path}：{error}") from error
        definitions = _rust_definitions(source, file_path)
    else:
        definitions = []
    entities.extend(_with_duplicate_lines(definitions))
    return entities


def collect_entities(repository, owners, nodes):
    """Return expected entity IDs grouped by the selected owning map elements."""
    root = Path(repository)
    entities = {node: [] for node in sorted(nodes)}
    for file_path, owner in sorted(owners.items()):
        if owner.element not in entities:
            continue
        entities[owner.element].extend(
            file_entities(root, file_path)
        )
    return entities
