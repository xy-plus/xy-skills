"""只读校验 handoff.json：合格退出 0，不合格逐条报错并退出 1，用法错误退出 2。"""
import json
import pathlib
import re
import sys


ORDINARY_FIELDS = {
    "kind": str,
    "from": {"name": str, "short_id": str},
    "task": {"what": str, "done_when": str, "others": str},
    "reply": str,
    "quotes": [{"text": str, "source": str}],
    "rules": str,
    "facts": [str],
    "decide_yourself": [str],
    "boundaries": [str],
}
CLEANUP_FIELDS = {
    "kind": str,
    "from": {"name": str, "short_id": str},
    "reply": str,
    "contract": str,
    "changed": [{"path": str, "in_use_by": str}],
}
REMOTE_ABSOLUTE_PATH = re.compile(r"[^@\s:/]+@[^@\s:/]+:(/.*)")


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
    if not isinstance(document, dict):
        return ["交接说明 必须是对象"]
    if "kind" not in document:
        return ["缺少字段：交接说明.kind"]
    kind = document["kind"]
    if kind not in ("普通", "清扫"):
        return ["kind 只能是「普通」或「清扫」"]

    shape = ORDINARY_FIELDS if kind == "普通" else CLEANUP_FIELDS
    problems = check_value("交接说明", document, shape)
    if "reply" in document:
        allowed_replies = ("要", "不要") if kind == "普通" else ("要",)
        if document["reply"] not in allowed_replies:
            problems.append("reply 只能是「要」或「不要」" if kind == "普通" else "reply 只能是「要」")

    if kind == "普通":
        if document.get("quotes") == []:
            problems.append("交接说明.quotes 不能为空")
        return problems

    if document.get("changed") == []:
        problems.append("交接说明.changed 不能为空")
    contract = document.get("contract")
    if isinstance(contract, str) and contract.strip():
        contract_path = pathlib.Path(contract)
        if not contract_path.is_absolute():
            problems.append("contract 必须是绝对路径")
        elif not contract_path.is_dir():
            problems.append("contract 必须是已存在的目录")
        else:
            quotes_path = contract_path / "quotes"
            if not quotes_path.is_dir():
                problems.append("contract 目录下缺少 quotes/")
            elif not any(re.fullmatch(r"[1-9]\d*\.md", path.name) for path in quotes_path.iterdir()
                         if path.is_file()):
                problems.append("quotes/ 里至少要有一个 <N>.md")

    changed = document.get("changed")
    if isinstance(changed, list):
        for index, item in enumerate(changed):
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if isinstance(path, str) and path.strip():
                local_absolute = pathlib.Path(path).is_absolute()
                remote_absolute = REMOTE_ABSOLUTE_PATH.fullmatch(path) is not None
                if not (local_absolute or remote_absolute):
                    problems.append(
                        f"交接说明.changed[{index}].path 必须是本机绝对路径或「用户@主机:/绝对路径」"
                    )
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
