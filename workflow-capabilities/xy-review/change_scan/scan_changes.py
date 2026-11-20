"""Scan committed changes for small cleanup issues before code review."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care/xy-repo-map"))

from duplicate_blocks import find_duplicate_windows, merge_ranges  # noqa: E402
from repo_map_lib.git_repository import gitlink_paths, run_git, tracked_paths  # noqa: E402
from repo_map_lib.map_data import file_category_with_skip  # noqa: E402
from repo_map_lib.skip_paths.skip_paths import is_skipped_path, read_skip_paths  # noqa: E402


RUFF_RULES = "F401,F841,ERA001,C901,PLR0913,PLR0915"
RUFF_RULES_SET = set(RUFF_RULES.split(","))
# Ruff defaults: complexity > 10, arguments > 5, statements > 50.
RUFF_MAX_COMPLEXITY = 10
RUFF_MAX_ARGUMENTS = 5
RUFF_MAX_STATEMENTS = 50
# pylint's too-many-lines default is 1000 physical lines.
MAX_FILE_LINES = 1000
CODE_CATEGORIES = {"code", "tests"}
SCANNED_CATEGORIES = {"code", "tests", "docs"}
RUST_TOKENS = re.compile(
    r"//[^\n]*|/\*.*?\*/|"
    r"br(\#*)\".*?\"\1|r(\#*)\".*?\"\2|"
    r"b?\"(?:\\.|[^\"\\])*\"|b?'(?:\\.|[^'\\])*'|"
    r"\b\d[\w.]*|[A-Za-z_][A-Za-z_0-9]*|::|=>|->|==|!=|<=|>=|&&|\|\||[^\s]",
    re.S,
)


class ScanError(Exception):
    """An invalid scan request or a failed required tool."""


def _run(command, cwd, label, accepted_codes):
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        raise ScanError(f"无法运行 {label}：{error}") from error
    if result.returncode not in accepted_codes:
        details = result.stderr.strip() or result.stdout.strip()
        raise ScanError(f"{label} 运行失败（退出码 {result.returncode}）：{details}")
    return result


def _git(repository, *arguments, accepted_codes):
    try:
        result = run_git(repository, *arguments, check=False)
    except (OSError, ValueError) as error:
        raise ScanError(f"git {' '.join(arguments[:2])} 运行失败：{error}") from error
    if result.returncode not in accepted_codes:
        details = result.stderr.strip() or result.stdout.strip()
        raise ScanError(
            f"git {' '.join(arguments[:2])} 运行失败（退出码 {result.returncode}）：{details}"
        )
    return result


def _regular_tracked_paths(repository):
    paths = tracked_paths(repository)
    gitlinks = gitlink_paths(repository)
    return paths, {
        path for path in paths
        if path not in gitlinks
        and not (repository / path).is_symlink()
        and (repository / path).is_file()
    }


def _merge_line_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [[start, end] for start, end in merged]


def _diff_new_line_ranges(repository, base, head, renamed_paths):
    if not renamed_paths:
        return {}
    result = _git(
        repository, "diff", "-M", "-U0", "--no-color", base, head,
        accepted_codes=(0,),
    )
    ranges = {path: [] for path in renamed_paths}
    current_path = None
    hunk_pattern = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    for line in result.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[6:]
            continue
        if line.startswith("+++ "):
            current_path = None
            continue
        match = hunk_pattern.match(line)
        if match is None or current_path not in ranges:
            continue
        new_start = int(match.group(1))
        new_count = int(match.group(2) or "1")
        if new_count:
            ranges[current_path].append((new_start, new_start + new_count - 1))
        else:
            # A deletion has no new line; include the lines on both sides of its point.
            ranges[current_path].append((max(1, new_start), max(1, new_start + 1)))
    return {
        path: _merge_line_ranges(path_ranges)
        for path, path_ranges in ranges.items()
    }


def _changed_paths(repository, base, head, regular_files):
    result = _git(
        repository, "diff", "-M", "--name-status", "-z", base, head,
        accepted_codes=(0,),
    )
    parts = result.stdout.split("\0")
    changed = set()
    renamed_paths = set()
    index = 0
    while index < len(parts) and parts[index]:
        status = parts[index]
        index += 1
        if status.startswith("R") or status.startswith("C"):
            old_path, new_path = parts[index:index + 2]
            index += 2
            if status.startswith("R") and status != "R100" and new_path in regular_files:
                renamed_paths.add(new_path)
                changed.add(new_path)
            elif status.startswith("C") and new_path in regular_files:
                changed.add(new_path)
        elif status in {"A", "M", "T"}:
            path = parts[index]
            index += 1
            if path in regular_files:
                changed.add(path)
        elif status == "D":
            index += 1
        else:
            raise ScanError(f"无法识别 git diff 改动状态：{status}")
    partial = _diff_new_line_ranges(repository, base, head, renamed_paths)
    return sorted(changed), partial


def _repository_and_revision(repository_value, base_value):
    requested = Path(repository_value)
    if not requested.is_absolute():
        raise ScanError("--repo 必须是仓库根目录的绝对路径")
    repository = requested.resolve()
    if not repository.is_dir():
        raise ScanError(f"--repo 不是目录：{repository}")
    top_level = Path(_git(
        repository, "rev-parse", "--show-toplevel", accepted_codes=(0,)
    ).stdout.strip()).resolve()
    if repository != top_level:
        raise ScanError(f"--repo 必须指向仓库根目录：{top_level}")

    status = _git(
        repository, "status", "--porcelain", "--untracked-files=all", accepted_codes=(0,)
    ).stdout
    if status:
        raise ScanError("工作树必须干净（git status --porcelain 有输出）")

    head = _git(
        repository, "rev-parse", "--verify", "HEAD^{commit}", accepted_codes=(0,)
    ).stdout.strip()
    if not base_value or base_value.startswith("-"):
        raise ScanError("--base 必须是 HEAD 的祖先提交且不能等于 HEAD")
    base_result = _git(
        repository,
        "rev-parse",
        "--verify",
        f"{base_value}^{{commit}}",
        accepted_codes=(0, 128),
    )
    if base_result.returncode:
        raise ScanError(f"--base 不是有效提交：{base_value}")
    base = base_result.stdout.strip()
    if base == head:
        raise ScanError("--base 不能等于 HEAD")
    ancestry = _git(
        repository,
        "merge-base",
        "--is-ancestor",
        base,
        head,
        accepted_codes=(0, 1),
    )
    if ancestry.returncode:
        raise ScanError("--base 必须是 HEAD 的祖先提交")
    return repository, base, head


def _skip_paths(repository, tracked_paths):
    skip_file = repository / "repo-map" / "skip.json"
    if "repo-map/skip.json" not in tracked_paths:
        return ()
    if skip_file.is_symlink() or not skip_file.is_file():
        raise ScanError("repo-map/skip.json 必须是普通文件")
    try:
        return read_skip_paths(repository, tracked_paths)
    except (OSError, ValueError) as error:
        raise ScanError(str(error)) from error


def _stable_hit(tool, rule, path, line, line_end, message):
    return {
        "id": f"{tool}:{rule}:{path}:{line}",
        "tool": tool,
        "path": path,
        "line": int(line),
        "lines": [int(line), int(line_end)],
        "message": message,
    }


def _duplicate_block_hits(repository, tracked_paths, skip_paths, changed):
    windows = find_duplicate_windows(repository, tracked_paths, skip_paths)
    pairs_by_changed_path = {}
    for locations in windows.values():
        for local in locations:
            if local[0] not in changed:
                continue
            partners = [other for other in locations if other != local]
            if partners:
                pairs_by_changed_path.setdefault(local[0], []).extend(
                    (local, other) for other in partners
                )

    hits = []
    for path, pairs in pairs_by_changed_path.items():
        local_ranges = merge_ranges(local for local, _other in pairs)
        for local_path, start, end in local_ranges:
            partners = merge_ranges(
                other
                for local, other in pairs
                if local[0] == local_path
                and local[1] <= end + 1
                and start <= local[2] + 1
            )
            locations = ", ".join(
                f"{other_path}:{other_start}-{other_end}"
                for other_path, other_start, other_end in partners
            )
            hits.append(_stable_hit(
                "duplicate-blocks",
                "six-lines-forty-characters",
                path,
                start,
                end,
                f"与 {locations} 有逐字重复段；连续 6 行且至少 40 个字母数字",
            ))
    return hits


def _line_count_hits(repository, changed, categories):
    hits = []
    for path in changed:
        if categories[path] not in SCANNED_CATEGORIES:
            continue
        try:
            lines = (repository / path).read_bytes().splitlines()
        except OSError as error:
            raise ScanError(f"无法读取改动文件 {path}：{error}") from error
        count = len(lines)
        if count > MAX_FILE_LINES:
            hits.append(_stable_hit(
                "file-lines",
                "over-1000-lines",
                path,
                MAX_FILE_LINES + 1,
                count,
                f"文件有 {count} 行，超过 1000 行门槛（pylint too-many-lines 默认值）",
            ))
    return hits


def _tool_path(name):
    path = shutil.which(name)
    if path is None:
        raise ScanError(f"缺少必需工具：{name}")
    return path


def _relative_path(repository, value):
    path = Path(value)
    if not path.is_absolute():
        path = repository / path
    return Path(os.path.relpath(path, repository)).as_posix()


def _function_line_range(repository, path, line):
    try:
        source = (repository / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ScanError(f"无法读取 Ruff 函数命中所在文件 {path}：{error}") from error
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        raise ScanError(f"无法解析 Ruff 函数命中所在文件 {path}：{error}") from error
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= line <= node.end_lineno
    ]
    if not matches:
        return [line, line]
    function = min(matches, key=lambda node: node.end_lineno - node.lineno)
    return [function.lineno, function.end_lineno]


def _ruff_hits(repository, changed, categories):
    paths = [
        path for path in changed
        if Path(path).suffix.lower() == ".py" and categories[path] in CODE_CATEGORIES
    ]
    if not paths:
        return []
    ruff = _tool_path("ruff")
    result = _run(
        [ruff, "check", "--isolated", "--select", RUFF_RULES,
         "--config", f"lint.mccabe.max-complexity={RUFF_MAX_COMPLEXITY}",
         "--config", f"lint.pylint.max-args={RUFF_MAX_ARGUMENTS}",
         "--config", f"lint.pylint.max-statements={RUFF_MAX_STATEMENTS}",
         "--output-format", "json", *paths],
        repository,
        "ruff",
        accepted_codes=(0, 1),
    )
    try:
        diagnostics = json.loads(result.stdout or "[]")
    except ValueError as error:
        raise ScanError(f"ruff 输出不是有效 JSON：{error}") from error
    if not isinstance(diagnostics, list):
        raise ScanError("ruff 输出不是诊断数组")
    hits = []
    function_ranges = {}
    for diagnostic in diagnostics:
        code = diagnostic.get("code")
        filename = _relative_path(repository, diagnostic.get("filename", ""))
        if code not in RUFF_RULES_SET or filename not in paths:
            continue
        location = diagnostic.get("location", {})
        line = location.get("row")
        message = diagnostic.get("message")
        if not isinstance(line, int) or not isinstance(message, str):
            raise ScanError("ruff 输出缺少行号或说明")
        if code in {"C901", "PLR0913", "PLR0915"}:
            if filename not in function_ranges:
                function_ranges[filename] = {}
            if line not in function_ranges[filename]:
                function_ranges[filename][line] = _function_line_range(
                    repository, filename, line
                )
            line_start, line_end = function_ranges[filename][line]
        else:
            line_start = line_end = line
        hits.append(_stable_hit(
            "ruff", code, filename, line_start, line_end, f"{code}: {message}"
        ))
    return hits


class _ReplaceLiterals(ast.NodeTransformer):
    def visit_Constant(self, node):
        node.value = "<literal>"
        node.kind = None
        return node


def _python_test_functions(source, path):
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        raise ScanError(f"无法解析 Python 文件 {path}：{error}") from error
    functions = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue
        normalized = _ReplaceLiterals().visit(
            ast.Module(body=node.body, type_ignores=[])
        )
        functions.append((
            node.name,
            ast.dump(normalized, include_attributes=False),
            node.lineno,
            node.end_lineno,
        ))
    return functions


def _matching_bracket(tokens, opening, left, right):
    depth = 0
    for index in range(opening, len(tokens)):
        if tokens[index] == left:
            depth += 1
        elif tokens[index] == right:
            depth -= 1
            if depth == 0:
                return index
    return None


def _is_rust_literal(token):
    return token in {"true", "false"} or token[0].isdigit() or token.startswith(
        ('"', "'", 'b"', "b'", 'r"', 'r#', 'br"', 'br#')
    )


def _test_functions_for_path(repository, path, categories):
    suffix = Path(path).suffix.lower()
    if categories.get(path) not in CODE_CATEGORIES or suffix not in {".py", ".rs"}:
        return None
    try:
        source = (repository / path).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        language = "Python" if suffix == ".py" else "Rust"
        raise ScanError(f"无法读取 {language} 测试 {path}：{error}") from error
    if suffix == ".py":
        return _python_test_functions(source, path)
    return _rust_test_functions(source)


def _rust_test_functions(source):
    tokens = [
        (match.group(), match.start())
        for match in RUST_TOKENS.finditer(source)
        if not match.group().startswith(("//", "/*"))
    ]
    words = [token for token, _offset in tokens]
    functions = []
    for index in range(len(words) - 3):
        if words[index:index + 4] != ["#", "[", "test", "]"]:
            continue
        function = _rust_test_structure(words, index + 4)
        if function is not None:
            name, structure, function_index, closing = function
            line_start = source.count("\n", 0, tokens[function_index][1]) + 1
            line_end = source.count("\n", 0, tokens[closing][1]) + 1
            functions.append((name, structure, line_start, line_end))
    return functions


def _rust_test_structure(words, start):
    function_index = next(
        (position for position in range(start, len(words)) if words[position] == "fn"),
        None,
    )
    if function_index is None or function_index + 2 >= len(words):
        return None
    parameters = next(
        (position for position in range(function_index + 2, len(words))
         if words[position] == "("),
        None,
    )
    if parameters is None:
        return None
    parameter_end = _matching_bracket(words, parameters, "(", ")")
    if parameter_end is None:
        return None
    opening = next(
        (position for position in range(parameter_end + 1, len(words))
         if words[position] == "{"),
        None,
    )
    if opening is None:
        return None
    closing = _matching_bracket(words, opening, "{", "}")
    if closing is None:
        return None
    body = [
        "<literal>" if _is_rust_literal(token) else token
        for token in words[opening:closing + 1]
    ]
    return words[function_index + 1], " ".join(body), function_index, closing


def _structural_test_hits(repository, tracked_paths, skip_paths, changed, categories):
    occurrences = {}
    for path in tracked_paths:
        if is_skipped_path(path, skip_paths):
            continue
        suffix = Path(path).suffix.lower()
        functions = _test_functions_for_path(repository, path, categories)
        if functions is None:
            continue
        for name, structure, line_start, line_end in functions:
            occurrences.setdefault((suffix, structure), []).append(
                (path, line_start, line_end, name)
            )

    hits = []
    for (suffix, _structure), members in occurrences.items():
        if len(members) < 2:
            continue
        for path, line_start, line_end, name in members:
            if path not in changed:
                continue
            others = sorted(
                f"{other_path}:{other_line}"
                for other_path, other_line, _other_end, _other_name in members
                if (other_path, other_line) != (path, line_start)
            )
            language = "Python" if suffix == ".py" else "Rust"
            hits.append(_stable_hit(
                "structural-tests",
                f"{language}:{name}",
                path,
                line_start,
                line_end,
                f"{language} 测试 {name} 去掉字面量后结构相同；其他位置：{', '.join(others)}",
            ))
    return hits


def _hit_is_in_partial_change(hit, partial):
    changed_ranges = partial.get(hit["path"])
    if changed_ranges is None:
        return True
    hit_start, hit_end = hit["lines"]
    return any(
        hit_start <= changed_end and changed_start <= hit_end
        for changed_start, changed_end in changed_ranges
    )


def scan_repository(repository_value, base_value):
    repository, base, head = _repository_and_revision(repository_value, base_value)
    tracked, regular_files = _regular_tracked_paths(repository)
    skip_paths = _skip_paths(repository, tracked)
    changed, partial = _changed_paths(repository, base, head, regular_files)
    changed = [path for path in changed if not is_skipped_path(path, skip_paths)]
    partial = {
        path: ranges for path, ranges in partial.items()
        if not is_skipped_path(path, skip_paths)
    }
    categories = {
        path: file_category_with_skip(repository, path, skip_paths)
        for path in tracked
    }
    changed_set = set(changed)

    # Whole-repository scans are needed to see callers and duplicate copies outside
    # this commit; only findings in changed paths belong in the review response.
    hits = _duplicate_block_hits(repository, tracked, skip_paths, changed_set)
    hits.extend(_line_count_hits(repository, changed, categories))
    hits.extend(_ruff_hits(repository, changed, categories))
    hits.extend(_structural_test_hits(
        repository, tracked, skip_paths, changed_set, categories
    ))
    hits = [hit for hit in hits if _hit_is_in_partial_change(hit, partial)]
    counts = {}
    ordered_hits = []
    for hit in sorted(
        hits,
        key=lambda hit: (
            hit["path"], hit["line"], hit["tool"], hit["id"], hit["message"]
        ),
    ):
        identifier = hit["id"]
        counts[identifier] = counts.get(identifier, 0) + 1
        if counts[identifier] > 1:
            hit["id"] = f"{identifier}:{counts[identifier]}"
        ordered_hits.append(hit)
    return {
        "repo": str(repository),
        "base": base,
        "head": head,
        "changed": changed,
        "partial": partial,
        "hits": ordered_hits,
    }


def main(argv):
    parser = argparse.ArgumentParser(description="扫描已提交改动中的清理命中")
    parser.add_argument("--repo", required=True, help="仓库根目录的绝对路径")
    parser.add_argument("--base", required=True, help="本轮基准提交")
    arguments = parser.parse_args(argv[1:])
    try:
        document = scan_repository(arguments.repo, arguments.base)
    except ScanError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
