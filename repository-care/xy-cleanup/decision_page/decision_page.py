import argparse
from dataclasses import dataclass
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


DECISION_SHAPE = {
    "title": "",
    "intro": [""],
    "groups": [
        {
            "title": "",
            "notes": [""],
            "items": [
                {
                    "id": "",
                    "title": "",
                    "lines": [""],
                    "options": [""],
                    "recommended": "",
                    "details": {"title": "", "lines": [""]},
                    "compare": [{"lines": "", "before": "", "after": ""}],
                }
            ],
        }
    ],
    "footnotes": [{"title": "", "lines": [""]}],
}
MAX_REQUEST_BYTES = 200 * 1024
# Release the single-threaded service when a connection stops making progress.
# Comparison pages are about 500 KB, so slow readers need a timeout per send,
# not one timeout for the entire response.
IDLE_TIMEOUT_SECONDS = 10
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_CARE = REPOSITORY_ROOT / "repository-care"
TEMPLATE = Path(__file__).resolve().parent / "templates" / "decision-page.html"
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_CARE))
from json_for_html import json_for_html  # noqa: E402
from json_validation import check_value, object_without_duplicate_keys  # noqa: E402
from portal_config import PLATFORM_PYTHON, PORTAL_SCRIPT  # noqa: E402
from text_metrics import count_non_whitespace_characters  # noqa: E402


def _decision_shape_view(value):
    """Adapt nullable comparison and detail values for the shared shape checker."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    groups = result.get("groups")
    if isinstance(groups, list):
        result["groups"] = [_decision_group_shape_view(group) for group in groups]
    return result


def _decision_group_shape_view(group):
    if not isinstance(group, dict):
        return group
    result = dict(group)
    items = result.get("items")
    if isinstance(items, list):
        result["items"] = [_decision_item_shape_view(item) for item in items]
    return result


def _decision_item_shape_view(item):
    if not isinstance(item, dict):
        return item
    result = dict(item)
    if "compare" in result:
        result["compare"] = _compare_shape_view(result["compare"])
    if "details" in result and result["details"] is None:
        result["details"] = {"title": "optional", "lines": ["optional"]}
    return result


def _compare_shape_view(compare):
    if compare is None:
        return [{"lines": "optional", "before": "optional", "after": "optional"}]
    if not isinstance(compare, list):
        return compare
    result = [dict(block) if isinstance(block, dict) else block for block in compare]
    for block in result:
        if isinstance(block, dict) and block.get("after") == "":
            block["after"] = "deleted"
    return result


def _decision_shape_error(value):
    problems = check_value("决定清单", _decision_shape_view(value), DECISION_SHAPE)
    if not problems:
        return None
    problem = problems[0]
    if "缺少字段：" in problem:
        location = problem.split("缺少字段：", 1)[1]
        field = location.rsplit(".", 1)[-1]
        return f"{location} 缺少字段 {field}"
    if "多余字段：" in problem:
        return problem.replace("多余字段：", "多余字段 ")
    if problem.endswith("不能为空"):
        return problem.removesuffix("不能为空") + "必须是非空字符串"
    if "必须是字符串" in problem:
        if ".after 必须是字符串" in problem:
            return problem
        return problem.replace("必须是字符串", "必须是非空字符串")
    if ".compare 必须是数组" in problem:
        return problem.replace("必须是数组", "必须是非空数组或 null")
    if problem.endswith("必须是数组"):
        location = problem.removesuffix("必须是数组")
        field = location.rsplit(".", 1)[-1].strip()
        if field in {"intro", "notes", "lines", "options"}:
            return location + "必须是字符串数组"
    return problem


def _parse_compare_line_range(value):
    if not isinstance(value, str):
        return None
    path, separator, numbers = value.rpartition(":")
    if not separator or not path.strip():
        return None
    parts = numbers.split("-")
    if len(parts) != 2 or any(
        not part.isascii() or not part.isdecimal() for part in parts
    ):
        return None
    start_line, end_line = (int(part) for part in parts)
    if start_line < 1 or end_line < start_line:
        return None
    return path, start_line, end_line


def _validate_compare(item_label, compare):
    if compare is None:
        return
    if not isinstance(compare, list) or not compare:
        raise ValueError(f"{item_label}.compare 必须是非空数组或 null")

    compare_path = None
    previous_start = None
    previous_end = None
    for index, block in enumerate(compare):
        block_label = f"{item_label}.compare[{index}]"
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
            raise ValueError(f"{block_label}.lines 必须按起始行递增：{block['lines']}")
        if previous_end is not None and start_line <= previous_end:
            raise ValueError(f"{block_label}.lines 与前一块不能重叠：{block['lines']}")
        previous_start = start_line
        previous_end = end_line


def _validate_details(item_label, details):
    if details is not None and not details["lines"]:
        raise ValueError(f"{item_label}.details.lines 必须是非空字符串数组")


def _validate_decision_item(item, item_label, item_ids):
    item_id = item["id"]
    if item_id in item_ids:
        raise ValueError(f"{item_label}.id 重复：{item_id}")
    item_ids.add(item_id)
    options = item["options"]
    if len(options) < 2:
        raise ValueError(f"{item_label}.options 至少需要两个选项")
    if len(set(options)) != len(options):
        raise ValueError(f"{item_label}.options 不能重复")
    if item["recommended"] not in options:
        raise ValueError(f"{item_label}.recommended 必须是 options 中的选项")
    _validate_compare(item_label, item["compare"])
    _validate_details(item_label, item["details"])


def _validate_decision_groups(groups):
    item_ids = set()
    for group_index, group in enumerate(groups):
        group_label = f"groups[{group_index}]"
        items = group["items"]
        if not items:
            raise ValueError(f"{group_label}.items 必须至少包含一条")
        for item_index, item in enumerate(items):
            _validate_decision_item(
                item,
                f"{group_label}.items[{item_index}]",
                item_ids,
            )


def validate_decisions(value):
    """Validate the fixed shape, then check decision-only constraints."""
    shape_error = _decision_shape_error(value)
    if shape_error:
        raise ValueError(shape_error)
    groups = value["groups"]
    if not groups:
        raise ValueError("groups 必须至少包含一组")
    _validate_decision_groups(groups)
    return value


def _comparison_rows(compare):
    rows = []
    unchanged_ranges = []

    def append_unchanged():
        if unchanged_ranges:
            rows.append(
                {
                    "kind": "unchanged",
                    "count": len(unchanged_ranges),
                    "ranges": list(unchanged_ranges),
                }
            )
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
            rows.append(
                {
                    "kind": "rewrite",
                    "lines": block["lines"],
                    "before": before,
                    "after": after,
                }
            )
    append_unchanged()
    return rows


def _comparison_data(compare):
    return {
        "before_chars": sum(
            count_non_whitespace_characters(block["before"]) for block in compare
        ),
        "after_chars": sum(
            count_non_whitespace_characters(block["after"]) for block in compare
        ),
        "rows": _comparison_rows(compare),
    }


def render_decision_page(decisions):
    """Read the decision-page template and embed safe data for one call."""
    return _render_decision_page(decisions)


def render_received_page(decisions, answers):
    """Render the submitted choices as a locked static page."""
    checked_answers = validate_answers({"answers": answers}, decisions)
    return _render_decision_page(decisions, answers=checked_answers)


def _render_decision_page(decisions, answers=None):
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
        "state": "received" if answers is not None else "pending",
    }
    if answers is not None:
        page_data["answers"] = answers
    embedded = json_for_html(page_data)
    template = TEMPLATE.read_text(encoding="utf-8")
    state_meta = (
        '<meta name="decision-state" content="received">'
        if answers is not None
        else ""
    )
    return (
        template.replace("__DECISION_STATE_META__", state_meta)
        .replace("__DECISION_DATA__", embedded)
    )


def validate_answers(payload, decisions):
    """Validate a complete response and return only its documented fields."""
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        raise ValueError("answers 必须是对象")
    items = {
        item["id"]: item for group in decisions["groups"] for item in group["items"]
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
            mode="w",
            encoding="utf-8",
            dir=answers_path.parent,
            prefix=f".{answers_path.name}.",
            suffix=".tmp",
            delete=False,
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
        self.submitted_answers = None
        self.submitted_event = threading.Event()
        self.timeout = 0.25
        super().__init__(str(socket_path), DecisionRequestHandler)

    def serve_until_complete(self, stop_event):
        while not stop_event.is_set():
            self.handle_request()


class DecisionRequestHandler(BaseHTTPRequestHandler):
    """Return the page and validate a single JSON answer submission."""

    def setup(self):
        self.request.settimeout(IDLE_TIMEOUT_SECONDS)
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
        self._write_body(body)
        self.close_connection = True

    def _write_body(self, body):
        self.wfile.flush()
        remaining = memoryview(body)
        while remaining:
            sent = self.request.send(remaining)
            if sent == 0:
                raise ConnectionError("连接在响应写完前关闭")
            remaining = remaining[sent:]

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

        if self.server.submitted_answers is not None:
            if answers == self.server.submitted_answers:
                self._send_json(200, {"received": True})
            else:
                self._send_json(409, {"error": "已经收到不同的回答"})
            return

        document = {"title": self.server.decisions["title"], "answers": answers}
        try:
            _write_answer_document(self.server.answers_path, document)
        except OSError as error:
            self._send_json(500, {"error": f"无法写入回答文件：{error}"})
            return

        self.server.submitted = True
        self.server.submitted_answers = answers
        self.server.submitted_event.set()
        self._send_json(200, {"received": True})

    def _send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self._write_body(body)
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


def _mount_configuration_uses_socket(name, socket_path):
    configuration = PORTAL_SCRIPT.parent / "apps" / f"{name}.caddy"
    try:
        content = configuration.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return str(socket_path) in content


def _received_site_index(name):
    portal_root = PORTAL_SCRIPT.parent
    site = portal_root / "sites" / name
    if not site.is_symlink():
        return None
    try:
        target = site.resolve(strict=True)
        published_root = (portal_root / "published").resolve(strict=True)
        if target.parent != published_root or not target.name.startswith(f"{name}-"):
            return None
        index = target / "index.html"
        page = index.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if '<meta name="decision-state" content="received">' not in page:
        return None
    return index


@dataclass
class AskServices:
    platform_runner: object
    stdout: object
    stderr: object
    stop_event: threading.Event


@dataclass
class AskCall:
    decisions: dict
    name: str
    answers_path: Path
    services: AskServices
    temporary: tempfile.TemporaryDirectory
    socket_path: Path
    server: object = None
    serving_thread: object = None
    mounted: bool = False
    mount_attempted: bool = False
    exit_code: int = 1


def _start_decision_server(call):
    page = render_decision_page(call.decisions)
    call.server = DecisionHTTPServer(
        call.socket_path, page, call.decisions, call.answers_path
    )
    call.serving_thread = threading.Thread(
        target=call.server.serve_until_complete,
        args=(call.services.stop_event,),
    )
    call.serving_thread.start()


def _write_platform_output(call, result):
    call.services.stdout.write(result.stdout)
    call.services.stderr.write(result.stderr)
    call.services.stdout.flush()
    call.services.stderr.flush()


def _mount_decision_page(call):
    mount_command = [
        PLATFORM_PYTHON,
        str(PORTAL_SCRIPT),
        "mount",
        call.name,
        "--app",
        str(call.socket_path),
    ]
    call.mount_attempted = True
    result = call.services.platform_runner(mount_command)
    _write_platform_output(call, result)
    if result.returncode == 0:
        call.mounted = True
        while not call.services.stop_event.is_set():
            if call.server.submitted_event.wait(0.25):
                break
        if call.server.submitted:
            _publish_received_page(call)


def _publish_received_page(call):
    directory = Path(call.temporary.name) / "received"
    directory.mkdir()
    (directory / "index.html").write_text(
        render_received_page(call.decisions, call.server.submitted_answers),
        encoding="utf-8",
    )

    unmount_command = [PLATFORM_PYTHON, str(PORTAL_SCRIPT), "unmount", call.name]
    unmount_result = call.services.platform_runner(unmount_command)
    _write_platform_output(call, unmount_result)
    if unmount_result.returncode != 0:
        raise RuntimeError("无法摘除决定页应用挂载")
    call.mounted = False

    call.services.stop_event.set()
    call.serving_thread.join()
    publish_command = [
        PLATFORM_PYTHON,
        str(PORTAL_SCRIPT),
        "publish",
        call.name,
        str(directory),
    ]
    publish_result = call.services.platform_runner(publish_command)
    _write_platform_output(call, publish_result)
    call.exit_code = 0 if publish_result.returncode == 0 else 1


def _unmount_previous_received_page(call):
    if _received_site_index(call.name) is None:
        return
    command = [PLATFORM_PYTHON, str(PORTAL_SCRIPT), "unmount", call.name]
    result = call.services.platform_runner(command)
    _write_platform_output(call, result)
    if result.returncode != 0:
        raise RuntimeError("无法替换已收到的静态决定页")


def _stop_decision_page(call):
    if call.serving_thread is not None:
        call.services.stop_event.set()
        call.serving_thread.join()
    if call.server is not None:
        call.server.server_close()
    owns_mount = call.mounted or (
        call.mount_attempted
        and _mount_configuration_uses_socket(call.name, call.socket_path)
    )
    if not owns_mount:
        return
    command = [PLATFORM_PYTHON, str(PORTAL_SCRIPT), "unmount", call.name]
    try:
        result = call.services.platform_runner(command)
        _write_platform_output(call, result)
        if result.returncode != 0:
            call.exit_code = 1
    except Exception as error:
        call.services.stderr.write(f"错误：无法摘除本次挂载：{error}\n")
        call.exit_code = 1


def run_ask(decisions, name, answers_path, services):
    """Mount the receiver, wait once, then remove only this invocation's mount."""
    temporary = tempfile.TemporaryDirectory(prefix="d", dir="/tmp")
    call = AskCall(
        decisions=decisions,
        name=name,
        answers_path=answers_path,
        services=services,
        temporary=temporary,
        socket_path=Path(temporary.name) / "s",
    )
    try:
        _unmount_previous_received_page(call)
        _start_decision_server(call)
        # The platform requests the page to verify the mount before mount returns.
        _mount_decision_page(call)
    except Exception as error:
        services.stderr.write(f"错误：决定页面服务失败：{error}\n")
        call.exit_code = 1
    finally:
        _stop_decision_page(call)
        temporary.cleanup()
    return call.exit_code


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
        services = AskServices(platform_runner, stdout, stderr, stop_event)
        return run_ask(decisions, args.name, answers_path, services)
    finally:
        _restore_stop_handlers(previous_handlers)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], _run_platform, sys.stdout, sys.stderr))
