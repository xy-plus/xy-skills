"""Small aggregation helpers shared by map-data composition."""
from .metrics import LineCounts


def add_counts(first, second):
    return LineCounts(first.code + second.code, first.docs + second.docs,
                      first.comments + second.comments, first.tests + second.tests)


def add_coverage(first, second):
    result = dict(first)
    for language, value in second.items():
        if language not in result:
            result[language] = value
        else:
            old_covered, old_total = result[language]
            new_covered, new_total = value
            result[language] = (old_covered + new_covered, old_total + new_total)
    return result


def coverage_text(items):
    if not items:
        return "不适用"
    parts = []
    for language in sorted(items):
        if language == "不适用":
            parts.append("不适用")
        else:
            covered, total = items[language]
            parts.append(f"{language} {covered}/{total}")
    return "；".join(parts)
