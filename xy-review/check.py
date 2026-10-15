"""检查一份 xy-review 报告的格式，确保没有遗漏。报告有两种：主审的审查报告（铁律 1、铁律 2、仓库规范、结论）
和剃刀的剃刀报告（铁律 3、结论），节集合必须正好是其中一种，节外、字段外、编号外没有内容；必填字段齐全、
不重复、写法标准（顶格、全角冒号、不加粗）、没有照抄模板占位符；「无」必须单独成项、独占一行，后面写对照了什么，
「本质」和「留下清单」不能写「无」；留下清单每行有实体和理由，删除清单每行有实体和删了丢什么，实体不能空；
仓库规范 1 到 7 条齐全且不重复；结论只有一行，是「通过」「改了就过」或「不通过」，并与各节一致：
通过时查问题的地方（偏离、问题、删除清单、仓库规范七条）全是「无」，另两种至少一处不是。

文件入口另查 group<G>/round<R>/ 路径、角色唯一、当前轮及全部前序轮的 submitted 送审版本，
从 group1/round1 起的全部前序记录和每组三轮上限。
判因报告固定为 group<G>/diagnosis.md，检查判因、结论两节及所在组的三轮材料。
退出码：0 检查合格；1 不合格，逐条打印问题和补齐责任人；2 用法错误。结论从报告里读。
"""
import pathlib
import re
import sys

# 三节铁律各自的必填字段。
LAW_FIELDS = {
    "铁律 1 第一性原理": ["本质", "偏离"],
    "铁律 2 难误用": ["问题"],
    "铁律 3 奥卡姆剃刀": ["留下清单", "删除清单", "旧代码删减"],
}
# 两种报告各自的节。
REPORT_KINDS = {
    "审查报告": ["铁律 1 第一性原理", "铁律 2 难误用", "仓库规范", "结论"],
    "剃刀报告": ["铁律 3 奥卡姆剃刀", "结论"],
}
RULE_COUNT = 7  # 仓库规范条数
# 不是「无」就算查出了问题的字段。「本质」「留下清单」本来就该有内容；「旧代码删减」另开任务处理，不计入结论。
PROBLEM_FIELDS = ["偏离", "问题", "删除清单"]
MUST_HAVE_CONTENT = ["本质", "留下清单"]
VERDICTS = ("通过", "改了就过", "不通过")
# 「无」必须单独成项：后面紧跟标点、括号或行尾；前面可以有列表符。「无法」「无用」这类词，以及「无 docstring」这种空格隔开的真发现，都不算「无」。
NONE_RE = re.compile(r"(?:[-*]\s+)?无(?:[，,、。；;：:（(]|$)")
BARE_NONE_RE = re.compile(r"(?:[-*]\s+)?无[，,、。；;：:（(]*[）)]?")
# 模板占位符就是 SKILL.md 各报告模板里尖括号包着的那些话，从那里读，不另抄一份。
TEMPLATE_PLACEHOLDERS = [
    piece
    for block in re.findall(r"```markdown\n(.*?)```", pathlib.Path(__file__).with_name("SKILL.md").read_text(encoding="utf-8"), re.S)
    for piece in re.findall(r"<([^<>]+)>", block)
]


def split_sections(text):
    """按「## 标题」切分，返回 ({标题: 行列表}, 问题列表)。重复的标题、第一节之前的内容都是问题；标题认不认识由 check 按报告种类判。"""
    sections, problems, name = {}, [], None
    for line in text.splitlines():
        heading = re.fullmatch(r"##\s+(.+?)\s*", line)
        if heading is None:
            if name is not None:
                sections[name].append(line)
            elif line.strip():
                problems.append(f"第一节之前不能有内容：{line.strip()}")
            continue
        name = heading.group(1)
        if name in sections:
            problems.append(f"「## {name}」出现了两次")
        sections.setdefault(name, [])
    return sections, problems


def split_fields(name, lines, keys):
    """「字段：值」，值可以接着写多行，直到下一个字段。字段名要顶格、全角冒号、不加粗，
    写法不标准的同名行当场报错，不能悄悄并进上一个字段；带列表符的行是清单内容，实体恰好叫字段名也不算。
    第一个字段之前不能有内容，否则那里写的发现没人看。返回 ({字段: 值}, 问题列表)。"""
    near_miss = re.compile(r"\W*(" + "|".join(map(re.escape, keys)) + r")\W*[:：]")
    fields, problems, key = {}, [], None
    for line in lines:
        matched = re.match(r"([^\s：]+)：(.*)", line)
        if matched and matched.group(1) in keys:
            key = matched.group(1)
            if key in fields:
                problems.append(f"「{key}」出现了两次")
            fields.setdefault(key, []).append(matched.group(2))
        elif not re.match(r"\s*[-*]\s", line) and near_miss.match(line):
            problems.append(f"「{near_miss.match(line).group(1)}」要顶格写、用全角冒号「：」、不加粗")
        elif key is not None:
            fields[key].append(line)
        elif line.strip():
            problems.append(f"「{name}」里字段之外有内容：{line.strip()}")
    return {k: "\n".join(v).strip() for k, v in fields.items()}, problems


def split_rules(lines):
    """「N. 内容」，内容可以接着写多行，直到下一个编号。第 1 条之前不能有内容。返回 ({N: 内容}, 问题列表)。"""
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
        elif line.strip():
            problems.append(f"仓库规范第 1 条之前有内容：{line.strip()}")
    return {k: "\n".join(v).strip() for k, v in rules.items()}, problems


def is_none(value):
    return NONE_RE.match(value) is not None


def has_placeholder(value):
    """尖括号里的话是模板占位符或它的片段，就是照抄模板没填。<仓库名>、<路径/文件> 这类不在模板里，不算。"""
    return any(len(piece) >= 2 and any(piece in template for template in TEMPLATE_PLACEHOLDERS)
               for piece in re.findall(r"<([^<>]+)>", value))


def check_value(where, value):
    """字段和规范条目的通用检查：非空、没抄占位符、写了「无」就要带对照且只有这一行。返回问题列表。"""
    if not value:
        return [f"{where}为空"]
    problems = []
    if has_placeholder(value):
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
    """返回问题列表，为空即格式合格。"""
    sections, problems = split_sections(text)
    kind = next((name for name, headings in REPORT_KINDS.items() if headings[0] in sections), None)
    if kind is None:
        problems.append("报告要么是" + "，要么是".join(f"{kind}（{'、'.join(names)}）" for kind, names in REPORT_KINDS.items()))
        return problems
    expected = REPORT_KINDS[kind]
    for name in sections:
        if name not in expected:
            problems.append(f"不认识的节「## {name}」，{kind}只能有：" + "、".join(expected))
    for name in expected:
        if name not in sections:
            problems.append(f"缺少「## {name}」")

    values, rules = {}, {}
    for name, keys in LAW_FIELDS.items():
        if name not in sections:
            continue
        fields, field_problems = split_fields(name, sections[name], keys)
        problems += field_problems
        for key in keys:
            if key not in fields:
                problems.append(f"{name}：缺少「{key}」")
                continue
            problems += check_value(f"{name}：「{key}」", fields[key])
            if key in MUST_HAVE_CONTENT and is_none(fields[key]):
                problems.append(f"{name}：「{key}」不能写「无」，它本来就该有内容")
            values[key] = fields[key]
    if "留下清单" in values:
        problems += check_lines("留下清单", values["留下清单"], "：", "实体：离开它，用户原话哪条做不到")
    if "删除清单" in values:
        problems += check_lines("删除清单", values["删除清单"], "；;", "实体；删了丢什么")
    if "仓库规范" in sections:
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
        return problems
    conclusion_lines = [line.strip() for line in sections["结论"] if line.strip()]
    verdict = conclusion_lines[0] if conclusion_lines else ""
    if len(conclusion_lines) != 1 or verdict not in VERDICTS:
        problems.append("结论只能有一行，写「通过」「改了就过」或「不通过」")
    elif verdict == "通过" and findings:
        problems.append("结论写通过，但有的地方不是「无，」开头：" + "、".join(findings))
    elif verdict != "通过" and not findings:
        problems.append(f"结论写{verdict}，但各节都是「无」")
    return problems


def check_diagnosis(text):
    """判因只查两节、非空原因和有序且不重复的处置，不重新编码各轮的阻断项。"""
    sections, problems = split_sections(text)
    for name in sections:
        if name not in ("判因", "结论"):
            problems.append(f"不认识的节「## {name}」，判因报告只能有：判因、结论")
    for name in ("判因", "结论"):
        if name not in sections:
            problems.append(f"缺少「## {name}」")
    if "判因" in sections:
        reason = "\n".join(sections["判因"]).strip()
        if not reason:
            problems.append("判因为空，要引用各轮报告的阻断项和证据说明这组为什么没过")
        if has_placeholder(reason):
            problems.append("判因还留着模板占位符")
    if "结论" in sections:
        lines = [line.strip() for line in sections["结论"] if line.strip()]
        actions = lines[0].split("、") if len(lines) == 1 else []
        if not actions or any(action not in ("拆分", "重想", "问用户", "顶回") for action in actions):
            problems.append("结论只能有一行，写「拆分」「重想」「问用户」「顶回」中的一种或几种，用「、」隔开")
        elif len(actions) != len(set(actions)):
            problems.append("结论里的走法不能重复")
    return problems


def check_round(directory, complete):
    """每轮须留送审版本；按内容认角色，当前轮可缺另一角色，前序失败轮须有作者处理记录。"""
    reports, problems, failed = {"主审": [], "剃刀": []}, [], False
    files = [path for path in sorted(directory.iterdir()) if path.is_file()] if directory.is_dir() else []
    if not any(path.name.startswith("submitted") for path in files):
        problems.append(f"缺少 {directory / 'submitted*'} 送审版本，请作者补齐")
    for path in files:
        if path.name == "handling.md" or path.name.startswith("submitted"):
            continue
        text = path.read_text(encoding="utf-8")
        sections, _ = split_sections(text)
        kind = next((name for name, headings in REPORT_KINDS.items() if headings[0] in sections), None)
        if kind is None:
            continue  # 派审说明等文件不是报告，不靠文件名猜角色。
        role = "主审" if kind == "审查报告" else "剃刀"
        reports[role].append(path)
        if complete:
            problems += [f"{path}：请{role}修正报告：{problem}" for problem in check(text)]
        verdict = [line.strip() for line in sections.get("结论", []) if line.strip()]
        failed |= verdict == ["不通过"]
    for role, paths in reports.items():
        if complete and not paths:
            problems.append(f"{directory}：缺少{role}报告，请作者安排{role}补齐")
        elif len(paths) > 1:
            problems.append(f"{directory}：同轮{role}报告只能有一份，发现："
                            + "、".join(path.name for path in paths) + "；请作者核对轮次，修订后在下一轮派审")
    if complete and failed and not (directory / "handling.md").is_file():
        problems.append(f"缺少 {directory / 'handling.md'}，请作者补写每条意见怎么处理、对象改了什么")
    return problems, failed


def check_path(path):
    """目录就是轮次记录；每次从头核整条历史，不依赖以前曾运行过检查。"""
    if path.name == "handling.md" or path.name.startswith("submitted"):
        return [f"{path}：普通报告不能使用保留名 handling.md 或 submitted*，这两类名字只给材料用；请作者另选报告文件名"]
    diagnosis = path.name == "diagnosis.md"
    group_directory = path.parent if diagnosis else path.parent.parent
    group_match = re.fullmatch(r"group([1-9][0-9]*)", group_directory.name)
    round_match = None if diagnosis else re.fullmatch(r"round([1-9][0-9]*)", path.parent.name)
    if group_match is None or (not diagnosis and round_match is None):
        if diagnosis:
            return [f"{path}：判因报告位置不合，请作者交给判因子代理写到 <对象目录>/group<G>/diagnosis.md，G 从 1 起"]
        return [f"{path}：报告位置不合，请作者指定 <对象目录>/group<G>/round<R>/<报告文件>，G 从 1 起，R 只能是 1、2、3"]

    group_number = int(group_match.group(1))
    round_number = 3 if diagnosis else int(round_match.group(1))
    problems = []
    for number in range(1, group_number + 1):
        group = group_directory.parent / f"group{number}"
        extra_rounds = [item.name for item in group.iterdir()
                        if item.is_dir() and re.fullmatch(r"round[0-9]+", item.name)
                        and int(item.name[5:]) > 3] if group.is_dir() else []
        if extra_rounds:
            return [f"{group} 出现 {'、'.join(sorted(extra_rounds))}，一组最多 3 轮；请作者先派判因子代理写 "
                    f"{group / 'diagnosis.md'}，按结论处理后再开 {group.parent / f'group{number + 1}'}"]

        completed_group = number < group_number or diagnosis
        last_round = 3 if completed_group else round_number
        previous_failed = False
        for current_round in range(1, last_round + 1):
            directory = group / f"round{current_round}"
            if current_round > 1 and not previous_failed:
                problems.append(f"{directory} 的上一轮 {group / f'round{current_round - 1}'} 没有「不通过」报告；"
                                "请作者核对结论，只有不通过、处理完才在同组下一轮再审")
            round_problems, previous_failed = check_round(directory, completed_group or current_round < round_number)
            problems += round_problems
        if completed_group and not previous_failed:
            problems.append(f"{group / 'round3'} 没有「不通过」报告，这组未满足失败条件；请作者核对结论，不能判因或另开下一组")
        if number < group_number:
            report = group / "diagnosis.md"
            if not report.is_file():
                problems.append(f"缺少 {report}，请作者派判因子代理补齐，按结论处理后再开下一组")
            else:
                problems += [f"{report}：请判因子代理修正报告：{problem}"
                             for problem in check_diagnosis(report.read_text(encoding="utf-8"))]

    text = path.read_text(encoding="utf-8")
    if diagnosis:
        problems += [f"{path}：请判因子代理修正报告：{problem}" for problem in check_diagnosis(text)]
    else:
        sections, _ = split_sections(text)
        role = ("主审" if REPORT_KINDS["审查报告"][0] in sections else
                "剃刀" if REPORT_KINDS["剃刀报告"][0] in sections else "审查者")
        problems += [f"{path}：请{role}修正报告：{problem}" for problem in check(text)]
    return problems


def main(argv):
    if len(argv) != 2:
        print("用法：check.py <报告路径>")
        return 2
    try:
        problems = check_path(pathlib.Path(argv[1]).absolute())
    except (OSError, UnicodeError) as error:
        problems = [f"请作者核对报告路径与 UTF-8 文件，并安排对应的审查者或判因子代理补齐：{error}"]
    if problems:
        print("\n".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
