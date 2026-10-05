"""检查一份对抗审查结果是否符合 SKILL.md 的「结果格式」：四节齐全，每节写了「查了：是」、
必填字段都非空、结论是「通过」或「不通过」。合格打印各节结论退出 0，否则逐条报错退出 1。"""
import re
import sys

# 每节的必填字段；「查了」「结论」每节都有，单独核。
SECTIONS = {
    "第一性原理": ["多做", "少做", "补丁"],
    "难误用": ["问题"],
    "奥卡姆剃刀": ["删除清单", "旧代码删减"],
    "仓库规范": ["问题"],
}


def parse_sections(text):
    sections, name = {}, None
    for line in text.splitlines():
        heading = re.fullmatch(r"## (.+?)\s*", line)
        if heading:
            name = heading.group(1)
            sections[name] = []
        elif name is not None:
            sections[name].append(line)
    return sections


def parse_fields(lines, keys):
    # 字段值是冒号后的文字加上接着的行，直到下一个已知字段。字段名后可带「（…）」注释，如「删除清单（共 3 条）：」。
    fields, key = {}, None
    for line in lines:
        match = re.match(r"([^\s（：]+)(?:（[^）]*）)?：(.*)", line)
        if match and match.group(1) in keys:
            key = match.group(1)
            fields[key] = [match.group(2)]
        elif key is not None:
            fields[key].append(line)
    return {k: "\n".join(v).strip() for k, v in fields.items()}


def check(text):
    problems, conclusions = [], []
    sections = parse_sections(text)
    for name, required in SECTIONS.items():
        if name not in sections:
            problems.append(f"缺少「## {name}」")
            continue
        fields = parse_fields(sections[name], {"查了", "结论", *required})
        if fields.get("查了") != "是":
            problems.append(f"{name}：缺少「查了：是」")
        for key in required:
            if not fields.get(key):
                problems.append(f"{name}：「{key}」为空")
        # 结论后可带「（…）」写理由，如「不通过（删除清单未清）」。
        verdict = re.fullmatch(r"(通过|不通过)(?:（.*）)?", fields.get("结论", ""), re.S)
        if verdict is None:
            problems.append(f"{name}：结论必须是「通过」或「不通过」")
        else:
            conclusions.append(f"{name}：{verdict.group(1)}")
    return problems, conclusions


def main(argv):
    if len(argv) != 2:
        print("用法：check.py <审查报告路径>")
        return 2
    with open(argv[1], encoding="utf-8") as f:
        problems, conclusions = check(f.read())
    for line in problems or conclusions:
        print(line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
