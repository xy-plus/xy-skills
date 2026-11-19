import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))
from json_validation import check_value, object_without_duplicate_keys  # noqa: E402


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


def check(document):
    problems = check_value("交接说明", document, FIELDS)
    if isinstance(document, dict):
        if document.get("quotes") == []:
            problems.append("交接说明.quotes 不能为空")
        if "reply" in document and document["reply"] not in ("要", "不要"):
            problems.append("reply 只能是「要」或「不要」")
    return problems


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：check_handoff.py <交接 JSON 路径>")
        sys.exit(2)
    try:
        with open(sys.argv[1], encoding="utf-8") as stream:
            document = json.load(stream, object_pairs_hook=object_without_duplicate_keys)
    except (OSError, ValueError) as error:
        print(f"无法读取 JSON「{sys.argv[1]}」：{error}")
        sys.exit(1)
    problems = check(document)
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    sys.exit(0)
