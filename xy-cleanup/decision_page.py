import argparse
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import secrets
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit


DECISION_KEYS = {"title", "intro", "groups", "footnotes"}
GROUP_KEYS = {"title", "notes", "items"}
ITEM_KEYS = {"id", "title", "lines", "options", "recommended", "compare"}
COMPARE_KEYS = {"lines", "before", "after"}
FOOTNOTE_KEYS = {"title", "lines"}
MAX_REQUEST_BYTES = 200 * 1024
REQUEST_READ_TIMEOUT_SECONDS = 10
REPOSITORY = Path(__file__).resolve().parents[1]
TEMPLATE = Path(__file__).resolve().parent / "templates" / "decision-page.html"
sys.path.insert(0, str(REPOSITORY / "xy-repo-map"))
sys.path.insert(0, str(REPOSITORY / "xy-handoff"))
from repo_map_lib.embedded_json import json_for_html  # noqa: E402
from repo_map_lib.publish_command import PLATFORM_PYTHON, PORTAL_SCRIPT  # noqa: E402
from check_handoff import object_without_duplicate_keys  # noqa: E402


def _require_object(value, expected_keys, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    missing = expected_keys - value.keys()
    extra = value.keys() - expected_keys
    if missing:
        field = sorted(missing)[0]
        raise ValueError(f"{label} 缺少字段 {field}")
    if extra:
        field = sorted(extra, key=str)[0]
        raise ValueError(f"{label} 有多余字段 {field}")


def _require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空字符串")


def _require_text_list(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} 必须是字符串数组")
    for index, text in enumerate(value):
        _require_text(text, f"{label}[{index}]")


def _parse_compare_line_range(value):
    if not isinstance(value, str):
        return None
    path, separator, numbers = value.rpartition(":")
    if not separator or not path.strip():
        return None
    parts = numbers.split("-")
    if (len(parts) != 2
            or any(not part.isascii() or not part.isdecimal() for part in parts)):
        return None
    start_line, end_line = (int(part) for part in parts)
    if start_line < 1 or end_line < start_line:
        return None
    return path, start_line, end_line


def validate_decisions(value):
    """Validate every decision-list field and return the original object."""
    _require_object(value, DECISION_KEYS, "决定清单")
    _require_text(value["title"], "title")
    _require_text_list(value["intro"], "intro")
    groups = value["groups"]
    if not isinstance(groups, list) or not groups:
        raise ValueError("groups 必须至少包含一组")

    item_ids = set()
    for group_index, group in enumerate(groups):
        group_label = f"groups[{group_index}]"
        _require_object(group, GROUP_KEYS, group_label)
        _require_text(group["title"], f"{group_label}.title")
        _require_text_list(group["notes"], f"{group_label}.notes")
        items = group["items"]
        if not isinstance(items, list) or not items:
            raise ValueError(f"{group_label}.items 必须至少包含一条")

        for item_index, item in enumerate(items):
            item_label = f"{group_label}.items[{item_index}]"
            _require_object(item, ITEM_KEYS, item_label)
            for field in ("id", "title"):
                _require_text(item[field], f"{item_label}.{field}")
            item_id = item["id"]
            if item_id in item_ids:
                raise ValueError(f"{item_label}.id 重复：{item_id}")
            item_ids.add(item_id)
            _require_text_list(item["lines"], f"{item_label}.lines")
            options = item["options"]
            if not isinstance(options, list) or len(options) < 2:
                raise ValueError(f"{item_label}.options 至少需要两个选项")
            _require_text_list(options, f"{item_label}.options")
            if len(set(options)) != len(options):
                raise ValueError(f"{item_label}.options 不能重复")
            _require_text(item["recommended"], f"{item_label}.recommended")
            if item["recommended"] not in options:
                raise ValueError(f"{item_label}.recommended 必须是 options 中的选项")
            compare = item["compare"]
            if compare is not None:
                if not isinstance(compare, list) or not compare:
                    raise ValueError(f"{item_label}.compare 必须是非空数组或 null")
                compare_path = None
                previous_start = None
                previous_end = None
                for compare_index, block in enumerate(compare):
                    block_label = f"{item_label}.compare[{compare_index}]"
                    _require_object(block, COMPARE_KEYS, block_label)
                    _require_text(block["lines"], f"{block_label}.lines")
                    line_range = _parse_compare_line_range(block["lines"])
                    if line_range is None:
                        raise ValueError(
                            f"{block_label}.lines 行范围无效，必须写成 路径:起始行-结束行：{block['lines']}"
                        )
                    path, start_line, end_line = line_range
                    if compare_path is None:
                        compare_path = path
                    elif path != compare_path:
                        raise ValueError(
                            f"{block_label}.lines 必须与同一 compare 的其他块使用同一个路径：{path}"
                        )
                    if previous_start is not None and start_line <= previous_start:
                        raise ValueError(
                            f"{block_label}.lines 必须按起始行递增：{block['lines']}"
                        )
                    if previous_end is not None and start_line <= previous_end:
                        raise ValueError(
                            f"{block_label}.lines 与前一块不能重叠：{block['lines']}"
                        )
                    previous_start = start_line
                    previous_end = end_line
                    _require_text(block["before"], f"{block_label}.before")
                    if not isinstance(block["after"], str):
                        raise ValueError(f"{block_label}.after 必须是字符串")

    footnotes = value["footnotes"]
    if not isinstance(footnotes, list):
        raise ValueError("footnotes 必须是数组")
    for footnote_index, footnote in enumerate(footnotes):
        footnote_label = f"footnotes[{footnote_index}]"
        _require_object(footnote, FOOTNOTE_KEYS, footnote_label)
        _require_text(footnote["title"], f"{footnote_label}.title")
        _require_text_list(footnote["lines"], f"{footnote_label}.lines")
    return value


def _non_whitespace_characters(text):
    return sum(not character.isspace() for character in text)


def _comparison_rows(compare):
    rows = []
    unchanged_ranges = []

    def append_unchanged():
        if unchanged_ranges:
            rows.append({
                "kind": "unchanged",
                "count": len(unchanged_ranges),
                "ranges": list(unchanged_ranges),
            })
            unchanged_ranges.clear()

    for block in compare:
        before = block["before"]
        after = block["after"]
        if before == after:
            unchanged_ranges.append(block["lines"])
        elif after == "":
            append_unchanged()
            rows.append({"kind": "delete", "lines": block["lines"], "text": before})
        else:
            append_unchanged()
            rows.append({
                "kind": "rewrite",
                "lines": block["lines"],
                "before": before,
                "after": after,
            })
    append_unchanged()
    return rows


def _comparison_data(compare):
    return {
        "before_chars": sum(_non_whitespace_characters(block["before"]) for block in compare),
        "after_chars": sum(_non_whitespace_characters(block["after"]) for block in compare),
        "rows": _comparison_rows(compare),
    }


def render_decision_page(decisions):
    """Read the decision-page template and embed safe data for one call."""
    comparisons = {
        item["id"]: _comparison_data(item["compare"])
        for group in decisions["groups"]
        for item in group["items"]
        if item["compare"] is not None
    }
    page_data = {
        "call_id": secrets.token_urlsafe(18),
        "decisions": decisions,
        "comparisons": comparisons,
    }
    embedded = json_for_html(page_data)
    template = TEMPLATE.read_text(encoding="utf-8")
    return template.replace("__DECISION_DATA__", embedded)


def validate_answers(payload, decisions):
    """Validate a complete response and return only its documented fields."""
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        raise ValueError("answers 必须是对象")
    items = {
        item["id"]: item
        for group in decisions["groups"]
        for item in group["items"]
    }
    answers = payload["answers"]
    missing = items.keys() - answers.keys()
    extra = answers.keys() - items.keys()
    if missing:
        item_id = sorted(missing)[0]
        raise ValueError(f"缺少回答：{item_id}")
    if extra:
        item_id = sorted(extra, key=str)[0]
        raise ValueError(f"有未知回答：{item_id}")

    checked = {}
    for item_id, item in items.items():
        answer = answers[item_id]
        if not isinstance(answer, dict):
            raise ValueError(f"{item_id} 的回答必须是对象")
        option = answer.get("option")
        if not isinstance(option, str) or option not in item["options"]:
            raise ValueError(f"{item_id} 的 option 不属于本条选项")
        note = answer.get("note")
        if not isinstance(note, str):
            raise ValueError(f"{item_id} 的 note 必须是字符串")
        checked[item_id] = {"option": option, "note": note}
    return checked


def _write_answer_document(answers_path, document):
    """Write a complete answer file and publish it without overwriting."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=answers_path.parent,
            prefix=f".{answers_path.name}.", suffix=".tmp", delete=False,
        ) as output:
            temporary_path = Path(output.name)
            json.dump(document, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.link(temporary_path, answers_path)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _preflight_answer_path(answers_path):
    """Check that the answer directory supports temporary writes and hard links."""
    with tempfile.NamedTemporaryFile(
        dir=answers_path.parent,
        prefix=f".{answers_path.name}.",
        suffix=".probe",
    ) as output:
        probe_path = Path(output.name)
    try:
        _write_answer_document(probe_path, {})
    finally:
        try:
            probe_path.unlink()
        except OSError:
            pass


class DecisionHTTPServer(socketserver.UnixStreamServer):
    """Serve one decision page and accept one complete answer on a Unix socket."""

    def __init__(self, socket_path, page, decisions, answers_path):
        self.page = page
        self.decisions = decisions
        self.answers_path = Path(answers_path)
        self.submitted = False
        self.timeout = 0.25
        super().__init__(str(socket_path), DecisionRequestHandler)

    def serve_until_complete(self, stop_event):
        while not self.submitted and not stop_event.is_set():
            self.handle_request()


class DecisionRequestHandler(BaseHTTPRequestHandler):
    """Return the page and validate a single JSON answer submission."""

    def setup(self):
        self.request.settimeout(REQUEST_READ_TIMEOUT_SECONDS)
        super().setup()

    def log_message(self, format_string, *arguments):
        return

    def do_GET(self):
        if urlsplit(self.path).path != "/":
            self._send_json(404, {"error": "找不到页面"})
            return
        body = self.server.page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_POST(self):
        if urlsplit(self.path).path != "/submit":
            self._send_json(404, {"error": "找不到提交入口"})
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self._send_json(400, {"error": "不支持分块请求体"})
            return
        length_text = self.headers.get("Content-Length")
        if length_text is None or not length_text.isdecimal():
            self._send_json(400, {"error": "缺少有效的 Content-Length"})
            return
        try:
            length = int(length_text)
        except ValueError:
            self._send_json(400, {"error": "Content-Length 超出可接受范围"})
            return
        if length > MAX_REQUEST_BYTES:
            self._send_json(400, {"error": "请求体不能超过 200 KB"})
            return
        try:
            payload = json.loads(
                self.rfile.read(length),
                object_pairs_hook=object_without_duplicate_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "请求体必须是有效 JSON"})
            return
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return
        try:
            answers = validate_answers(payload, self.server.decisions)
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return

        document = {"title": self.server.decisions["title"], "answers": answers}
        try:
            _write_answer_document(self.server.answers_path, document)
        except OSError as error:
            self._send_json(500, {"error": f"无法写入回答文件：{error}"})
            return

        self.server.submitted = True
        self._send_json(200, {"received": True})

    def _send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


def make_parser():
    """Build the command-line parser with every input required."""
    result = argparse.ArgumentParser(description="在网页上收集清扫决定")
    commands = result.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="发布一页决定并等待一次回答")
    ask.add_argument("--decisions", required=True, help="决定清单 JSON 的路径")
    ask.add_argument("--name", required=True, help="平台站点名")
    ask.add_argument("--answers", required=True, help="尚不存在的回答文件路径")
    return result


def _load_decisions(path):
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=object_without_duplicate_keys,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"无法读取决定清单：{error}") from error
    return validate_decisions(value)


def _run_platform(arguments):
    return subprocess.run(arguments, capture_output=True, text=True, check=False)


def run_ask(decisions, name, answers_path, platform_runner, stdout, stderr, stop_event):
    """Mount the receiver, wait once, then remove only this invocation's mount."""
    temporary = tempfile.TemporaryDirectory(prefix="d", dir="/tmp")
    socket_path = Path(temporary.name) / "s"
    server = None
    serving_thread = None
    mounted = False
    exit_code = 1
    try:
        page = render_decision_page(decisions)
        server = DecisionHTTPServer(socket_path, page, decisions, answers_path)
        serving_thread = threading.Thread(
            target=server.serve_until_complete,
            args=(stop_event,),
        )
        serving_thread.start()
        # The platform requests the page to verify the mount before mount returns.
        mount_command = [
            PLATFORM_PYTHON,
            str(PORTAL_SCRIPT),
            "mount",
            name,
            "--app",
            str(socket_path),
        ]
        mount_result = platform_runner(mount_command)
        stdout.write(mount_result.stdout)
        stderr.write(mount_result.stderr)
        stdout.flush()
        stderr.flush()
        if mount_result.returncode == 0:
            mounted = True
            serving_thread.join()
            exit_code = 0 if server.submitted else 1
    except Exception as error:
        stderr.write(f"错误：决定页面服务失败：{error}\n")
        exit_code = 1
    finally:
        if serving_thread is not None:
            stop_event.set()
            serving_thread.join()
        if server is not None:
            server.server_close()
        if mounted:
            unmount_command = [PLATFORM_PYTHON, str(PORTAL_SCRIPT), "unmount", name]
            try:
                unmount_result = platform_runner(unmount_command)
                stdout.write(unmount_result.stdout)
                stderr.write(unmount_result.stderr)
                stdout.flush()
                stderr.flush()
                if unmount_result.returncode != 0:
                    exit_code = 1
            except Exception as error:
                stderr.write(f"错误：无法摘除本次挂载：{error}\n")
                exit_code = 1
        temporary.cleanup()
    return exit_code


def _install_stop_handlers(stop_event):
    if threading.current_thread() is not threading.main_thread():
        return {}
    previous = {}

    def request_stop(signal_number, frame):
        stop_event.set()

    for signal_number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[signal_number] = signal.signal(signal_number, request_stop)
    return previous


def _restore_stop_handlers(previous):
    for signal_number, handler in previous.items():
        signal.signal(signal_number, handler)


def main(argv, platform_runner, stdout, stderr):
    """Validate inputs and run the decision page command."""
    args = make_parser().parse_args(argv)
    requested_answers_path = Path(args.answers).expanduser()
    try:
        decisions = _load_decisions(args.decisions)
    except ValueError as error:
        stderr.write(f"错误：{error}\n")
        return 2
    if os.path.lexists(requested_answers_path):
        stderr.write(f"错误：回答文件已经存在：{requested_answers_path}\n")
        return 2
    answers_path = requested_answers_path.resolve()
    try:
        _preflight_answer_path(answers_path)
    except OSError as error:
        stderr.write(f"错误：回答文件路径无法保存：{error}\n")
        return 2

    stop_event = threading.Event()
    previous_handlers = _install_stop_handlers(stop_event)
    try:
        return run_ask(decisions, args.name, answers_path, platform_runner,
                       stdout, stderr, stop_event)
    finally:
        _restore_stop_handlers(previous_handlers)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], _run_platform, sys.stdout, sys.stderr))
