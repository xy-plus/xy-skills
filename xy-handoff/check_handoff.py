"""只读校验 handoff.json：合格退出 0，不合格逐条报错并退出 1，用法错误退出 2。"""
import json
import sys


FIELDS = {
    "from": {"name": str, "short_id": str},
    "task": {"what": str, "done_when": str, "others": str},
    "reply": str,
    "quotes": [{"text": str, "source": str}],
    "rules": str,
    "facts": [str],
    "decide_yourself": [str],
    "boundaries": [str],
}


def object_without_duplicate_keys(pairs):
    """解码每层对象时拒绝重复键，避免后值覆盖前值。"""
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"重复键：{key}")
        document[key] = value
    return document


def check_value(where, value, shape):
    """按固定结构检查每个字段，返回所有问题及其位置。"""
    if isinstance(shape, dict):
        if not isinstance(value, dict):
            return [f"{where} 必须是对象"]
        problems = [f"{where} 多余字段：{key}" for key in sorted(value.keys() - shape.keys())]
        for key, field_shape in shape.items():
            if key not in value:
                problems.append(f"缺少字段：{where}.{key}")
            else:
                problems += check_value(f"{where}.{key}", value[key], field_shape)
        return problems
    if isinstance(shape, list):
        if not isinstance(value, list):
            return [f"{where} 必须是数组"]
        return [problem for index, item in enumerate(value)
                for problem in check_value(f"{where}[{index}]", item, shape[0])]
    if not isinstance(value, str):
        return [f"{where} 必须是字符串"]
    return [] if value.strip() else [f"{where} 不能为空"]


def check(document):
    """返回问题列表，为空即合格。"""
    problems = check_value("交接说明", document, FIELDS)
    if isinstance(document, dict):
        if document.get("quotes") == []:
            problems.append("交接说明.quotes 不能为空")
        if "reply" in document and document["reply"] not in ("要", "不要"):
            problems.append("reply 只能是「要」或「不要」")
    return problems


def main(argv):
    if len(argv) != 2:
        print("用法：check_handoff.py <交接 JSON 路径>")
        return 2
    try:
        with open(argv[1], encoding="utf-8") as stream:
            document = json.load(stream, object_pairs_hook=object_without_duplicate_keys)
    except (OSError, ValueError) as error:
        print(f"无法读取 JSON「{argv[1]}」：{error}")
        return 1
    problems = check(document)
    if problems:
        print("\n".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
