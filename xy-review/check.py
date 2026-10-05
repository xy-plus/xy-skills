"""检查一份 xy-review 报告的格式，确保没有遗漏：只有规定的五节且不重复；必填字段齐全、不重复、
写法标准（顶格、全角冒号、不加粗）、不是模板占位符；「无」必须单独成项、独占一行、后面写对照了什么；
留下清单每行有实体和理由，删除清单每行有实体和删了丢什么，实体不能空；仓库规范 1 到 7 条齐全且不重复；
结论只有一行，是「通过」「改了就过」或「不通过」，并与各节一致：通过时各节全是「无」，另两种至少一处不是。

退出码：0 通过；3 改了就过；4 不通过；1 格式不合格，逐条打印问题；2 用法错误。
"""
import re
import sys

# 三节铁律各自的必填字段。
LAW_FIELDS = {
    "铁律 1 第一性原理": ["本质", "偏离"],
    "铁律 2 难误用": ["问题"],
    "铁律 3 奥卡姆剃刀": ["留下清单", "删除清单", "旧代码删减"],
}
SECTIONS = [*LAW_FIELDS, "仓库规范", "结论"]
RULE_COUNT = 7  # 仓库规范条数
# 不是「无」就算查出了问题的字段。「本质」「留下清单」本来就该有内容；「旧代码删减」另开任务处理，不阻塞本次通过。
PROBLEM_FIELDS = ["偏离", "问题", "删除清单"]
VERDICT_EXIT = {"通过": 0, "改了就过": 3, "不通过": 4}
# 「无」必须单独成项：后面紧跟标点、括号、空白或行尾；前面可以有列表符。「无法」「无用」这类词不算「无」。
NONE_RE = re.compile(r"(?:[-*]\s+)?无(?:[，,、。；;：:（(\s]|$)")
BARE_NONE_RE = re.compile(r"(?:[-*]\s+)?无[，,、。；;：:（(\s]*[）)]?")
# 模板占位符：尖括号里带中文。代码里的 Vec<T> 这类不带中文，不算。
PLACEHOLDER_RE = re.compile(r"<[^<>]*[一-鿿][^<>]*>")


def split_sections(text):
    """按「## 标题」切分，返回 ({标题: 行列表}, 问题列表)。不认识的标题和重复的标题都是问题。"""
    sections, problems, name = {}, [], None
    for line in text.splitlines():
        heading = re.fullmatch(r"##\s+(.+?)\s*", line)
        if heading is None:
            if name is not None:
                sections[name].append(line)
            continue
        name = heading.group(1)
        if name not in SECTIONS:
            problems.append(f"不认识的节「## {name}」，报告里只能有规定的五节")
        elif name in sections:
            problems.append(f"「## {name}」出现了两次")
        sections.setdefault(name, [])
    return sections, problems


def split_fields(lines, keys):
    """「字段：值」，值可以接着写多行，直到下一个字段。字段名要顶格、全角冒号、不加粗，
    写法不标准的同名行当场报错，不能悄悄并进上一个字段；带列表符的行是清单内容，实体恰好叫字段名也不算。
    返回 ({字段: 值}, 问题列表)。"""
    near_miss = re.compile(r"\W*(" + "|".join(map(re.escape, keys)) + r")\W*[:：]")
    fields, problems, key = {}, [], None
    for line in lines:
        matched = re.match(r"([^\s：]+)：(.*)", line)
        if matched and matched.group(1) in keys:
            key = matched.group(1)
            if key in fields:
                problems.append(f"「{key}」出现了两次")
            fields.setdefault(key, []).append(matched.group(2))
        elif not re.match(r"[-*]\s", line) and near_miss.match(line):
            problems.append(f"「{near_miss.match(line).group(1)}」要顶格写、用全角冒号「：」、不加粗")
        elif key is not None:
            fields[key].append(line)
    return {k: "\n".join(v).strip() for k, v in fields.items()}, problems


def split_rules(lines):
    """「N. 内容」，内容可以接着写多行，直到下一个编号。返回 ({N: 内容}, 问题列表)。"""
    rules, problems, number = {}, [], None
    for line in lines:
        matched = re.match(r"(\d+)\.\s*(.*)", line)
        if matched:
            number = int(matched.group(1))
            if not 1 <= number <= RULE_COUNT:
                problems.append(f"仓库规范只有 1 到 {RULE_COUNT} 条，出现了第 {number} 条")
            elif number in rules:
                problems.append(f"仓库规范第 {number} 条出现了两次；子列表要缩进，不要顶格编号")
            rules.setdefault(number, []).append(matched.group(2))
        elif number is not None:
            rules[number].append(line)
    return {k: "\n".join(v).strip() for k, v in rules.items()}, problems


def is_none(value):
    return NONE_RE.match(value) is not None


def check_value(where, value):
    """字段和规范条目的通用检查：非空、不是占位符、写了「无」就要带对照且只有这一行。返回问题列表。"""
    if not value:
        return [f"{where}为空"]
    problems = []
    if PLACEHOLDER_RE.search(value):
        problems.append(f"{where}还留着模板占位符")
    if is_none(value):
        if BARE_NONE_RE.fullmatch(value):
            problems.append(f"{where}只写了「无」，后面要写对照了什么")
        if len(value.splitlines()) > 1:
            problems.append(f"{where}写了「无」就只能有这一行，下面不能再列条目")
    return problems


def check_lines(where, value, separators, shape):
    """清单一行一样：去掉列表符后，分隔符前后都要有字；写「无」的清单不查行。"""
    if is_none(value):
        return []
    problems = []
    for line in value.splitlines():
        item = re.sub(r"^[-*]\s+", "", line.strip())
        parts = re.fullmatch(rf"([^{separators}]*)[{separators}](.*)", item)
        if item and not (parts and parts.group(1).strip() and parts.group(2).strip()):
            problems.append(f"{where}每行要写「{shape}」，这行不是：{line.strip()}")
    return problems


def check(text):
    """返回 (问题列表, 结论)。问题列表为空即格式合格，结论是「通过」或「不通过」。"""
    sections, problems = split_sections(text)
    values, rules = {}, {}

    for name, keys in LAW_FIELDS.items():
        if name not in sections:
            problems.append(f"缺少「## {name}」")
            continue
        fields, field_problems = split_fields(sections[name], keys)
        problems += field_problems
        for key in keys:
            if key not in fields:
                problems.append(f"{name}：缺少「{key}」")
                continue
            problems += check_value(f"{name}：「{key}」", fields[key])
            values[key] = fields[key]
    if "留下清单" in values:
        problems += check_lines("留下清单", values["留下清单"], "：", "实体：离开它，用户的哪个要求做不到")
    if "删除清单" in values:
        problems += check_lines("删除清单", values["删除清单"], "；;", "实体；删了丢什么")

    if "仓库规范" not in sections:
        problems.append("缺少「## 仓库规范」")
    else:
        rules, rule_problems = split_rules(sections["仓库规范"])
        problems += rule_problems
        for number in range(1, RULE_COUNT + 1):
            if number not in rules:
                problems.append(f"仓库规范第 {number} 条缺失")
            else:
                problems += check_value(f"仓库规范第 {number} 条", rules[number])

    # 查出了问题的地方，带上首行，报错时让人看出是写法问题还是真发现。
    findings = [f"「{key}」（{values[key].splitlines()[0]}）" for key in PROBLEM_FIELDS
                if values.get(key) and not is_none(values[key])]
    findings += [f"仓库规范第 {number} 条（{rules[number].splitlines()[0]}）" for number in range(1, RULE_COUNT + 1)
                 if rules.get(number) and not is_none(rules[number])]

    if "结论" not in sections:
        problems.append("缺少「## 结论」")
        return problems, ""
    conclusion_lines = [line.strip() for line in sections["结论"] if line.strip()]
    verdict = conclusion_lines[0] if conclusion_lines else ""
    if len(conclusion_lines) != 1 or verdict not in VERDICT_EXIT:
        problems.append("结论只能有一行，写「通过」「改了就过」或「不通过」")
    elif verdict == "通过" and findings:
        problems.append("结论写通过，但有的地方不是「无，」开头：" + "、".join(findings))
    elif verdict != "通过" and not findings:
        problems.append(f"结论写{verdict}，但各节都是「无」")
    return problems, verdict


def main(argv):
    if len(argv) != 2:
        print("用法：check.py <报告路径>")
        return 2
    with open(argv[1], encoding="utf-8") as f:
        problems, verdict = check(f.read())
    if problems:
        print("\n".join(problems))
        return 1
    return VERDICT_EXIT[verdict]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
