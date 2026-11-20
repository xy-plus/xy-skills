import json
from pathlib import Path
import argparse
import subprocess
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


def read_document(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=object_without_duplicate_keys)


def start(handoff_path, directory, name):
    try:
        handoff = Path(handoff_path).resolve(strict=True)
        document = read_document(handoff)
    except (OSError, ValueError) as error:
        print(f"无法读取 JSON「{handoff_path}」：{error}")
        return 1
    problems = check(document)
    if problems:
        print("\n".join(problems))
        return 1
    try:
        work_directory = Path(directory).resolve(strict=True)
        if not work_directory.is_dir():
            raise NotADirectoryError(work_directory)
    except OSError as error:
        print(f"启动目录不可用「{directory}」：{error}")
        return 1
    if not name.strip() or any(char in name for char in "\x00\r\n"):
        print("会话名字不能为空或包含控制字符")
        return 2
    prompt = (f"请先读交接说明 {handoff}，然后照做。这件事由你全权负责，"
              "结论按交接说明里的回复方式汇报。")
    try:
        return subprocess.run(["claude", "--bg", "-n", name, prompt],
                              cwd=work_directory, check=False).returncode
    except OSError as error:
        print(f"无法启动 claude：{error}", file=sys.stderr)
        return 1


def main(arguments):
    if arguments and arguments[0] == "start":
        parser = argparse.ArgumentParser(description="检查交接说明并启动接手会话")
        parser.add_argument("operation", choices=("start",))
        parser.add_argument("handoff_path")
        parser.add_argument("--dir", dest="directory", required=True)
        parser.add_argument("--name", required=True)
        options = parser.parse_args(arguments)
        return start(options.handoff_path, options.directory, options.name)
    if len(arguments) != 1:
        print("用法：check_handoff.py <交接 JSON 路径>")
        return 2
    parser = argparse.ArgumentParser(description="检查交接说明")
    parser.add_argument("handoff_path")
    options = parser.parse_args(arguments)
    try:
        document = read_document(options.handoff_path)
    except (OSError, ValueError) as error:
        print(f"无法读取 JSON「{options.handoff_path}」：{error}")
        return 1
    problems = check(document)
    if problems:
        print("\n".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
