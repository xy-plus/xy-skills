"""Read numbered rule text and check continuous citations to local rule files."""
from functools import lru_cache
from pathlib import Path
import re


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RULE_FILES = {
    "xy-iron-law": REPOSITORY_ROOT / "xy-iron-law" / "SKILL.md",
    "xy-repo-rules": REPOSITORY_ROOT / "xy-repo-rules" / "SKILL.md",
}
RULE_SOURCE = re.compile(r"(xy-iron-law|xy-repo-rules) 第 ([1-9][0-9]*) 条")
RULE_NAMES = tuple(RULE_FILES)


@lru_cache(maxsize=None)
def _numbered_rules(skill_path):
    """Read and index all numbered rules in one local skill document once."""
    try:
        lines = Path(skill_path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ()
    rules = []
    index = 0
    while index < len(lines):
        match = re.match(r"^\s*([1-9][0-9]*)\.\s", lines[index])
        if match is None:
            index += 1
            continue
        end = index + 1
        while end < len(lines) and re.match(r"^\s*[1-9][0-9]*\.\s", lines[end]) is None:
            end += 1
        rules.append((int(match.group(1)), "\n".join(lines[index:end])))
        index = end
    return tuple(rules)


def numbered_text(skill_path, number):
    """Return one numbered rule from a local skill document."""
    return dict(_numbered_rules(Path(skill_path))).get(number, "")


def numbered_rule_numbers(skill_path):
    """Return the rule numbers present in one local skill document."""
    return tuple(number for number, _ in _numbered_rules(Path(skill_path)))


def is_rule_source(source):
    """Return whether a source claims to cite one of the two rule documents."""
    return isinstance(source, str) and any(
        source == name or source.startswith(f"{name} ") for name in RULE_NAMES
    )


def rule_text(source):
    """Return the complete local rule named by a canonical source string."""
    if not isinstance(source, str):
        return ""
    match = RULE_SOURCE.fullmatch(source)
    if match is None:
        return ""
    name, number = match.groups()
    return numbered_text(RULE_FILES[name], int(number))


def rule_quote_problem(source, text):
    """Return a problem for an invalid rule quote, or None for other sources."""
    if not is_rule_source(source):
        return None
    rule = rule_text(source)
    if not rule:
        return f"规范条款来源格式错误或条号不存在：{source}"
    if not isinstance(text, str) or not text.strip() or text not in rule:
        return f"必须逐字引用「{source}」所标条款中的连续原文；所标条款原文：\n{rule}"
    return None


def basis_rule(basis):
    """Return the complete local rule named by a cleanup finding's basis."""
    if not isinstance(basis, dict):
        return ""
    return rule_text(basis.get("source"))


def basis_is_supported(basis):
    """Check a cleanup basis has an exact rule source and a continuous quote."""
    if not isinstance(basis, dict) or set(basis) != {"source", "text"}:
        return False
    source = basis.get("source")
    text = basis.get("text")
    rule = rule_text(source)
    return (
        is_rule_source(source)
        and bool(rule)
        and isinstance(text, str)
        and bool(text.strip())
        and text in rule
    )
