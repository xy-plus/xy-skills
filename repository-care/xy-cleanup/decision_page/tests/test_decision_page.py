"""Test the decision-page command and its request handling."""

from contextlib import contextmanager, nullcontext
import copy
import http.client
import io
import json
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
DECISION_PAGE_DIRECTORY = ROOT / "repository-care" / "xy-cleanup" / "decision_page"
sys.path.insert(0, str(DECISION_PAGE_DIRECTORY))

import decision_page  # noqa: E402


def valid_decisions():
    return {
        "title": "Cleanup decisions",
        "intro": ["Read every item."],
        "groups": [
            {
                "title": "Review",
                "notes": [],
                "items": [
                    {
                        "id": "A1",
                        "title": "Feature",
                        "lines": ["Keep this feature."],
                        "options": ["Keep", "Remove"],
                        "recommended": "Keep",
                        "compare": None,
                        "details": None,
                    }
                ],
            }
        ],
        "footnotes": [],
    }


def decisions_with_large_page():
    decisions = valid_decisions()
    decisions["intro"] = ["x" * (2 * 1024 * 1024)]
    return decisions


@contextmanager
def large_page_request(directory, answers):
    decisions = decisions_with_large_page()
    with patch.object(decision_page, "IDLE_TIMEOUT_SECONDS", 0.5):
        with decision_receiver(directory, decisions, answers) as (socket_path, _):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(socket_path)
            client.sendall(
                b"GET / HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            )
            try:
                yield socket_path, client
            finally:
                client.close()


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def request_socket(socket_path, method, path, data, timeout=None):
    body = None if data is None else json.dumps(data).encode("utf-8")
    headers = {"Connection": "close"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    connection = UnixSocketHTTPConnection(socket_path, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def request_raw_socket(socket_path, raw_body, content_length):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(socket_path)
    body = b"" if raw_body is None else raw_body.encode("utf-8")
    length = len(body) if content_length is None else content_length
    headers = (
        b"POST /submit HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n"
        b"Content-Type: application/json\r\nContent-Length: "
        + str(length).encode("ascii")
        + b"\r\n\r\n"
    )
    client.sendall(headers + body)
    try:
        response = http.client.HTTPResponse(client)
        response.begin()
        result = response.status, response.read()
    except (ConnectionError, http.client.HTTPException, OSError):
        result = None, b""
    finally:
        client.close()
    return result


@contextmanager
def decision_receiver(directory, decisions, answers):
    socket_path = directory / "s"
    server = decision_page.DecisionHTTPServer(
        socket_path,
        decision_page.render_decision_page(decisions),
        decisions,
        answers,
    )
    stop_event = threading.Event()
    serving = threading.Thread(
        target=server.serve_until_complete,
        args=(stop_event,),
        daemon=True,
    )
    serving.start()
    try:
        yield str(socket_path), serving
    finally:
        stop_event.set()
        serving.join(2)
        server.server_close()
        socket_path.unlink(missing_ok=True)


def run_ask(decisions, answers, runner):
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = decision_page.main(
        [
            "ask",
            "--decisions",
            str(decisions),
            "--name",
            "decision-test",
            "--answers",
            str(answers),
        ],
        runner,
        stdout,
        stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


@contextmanager
def running_ask(decisions_path, answers_path, portal_script=None):
    portal_patcher = (
        patch.object(decision_page, "PORTAL_SCRIPT", portal_script)
        if portal_script is not None
        else nullcontext()
    )
    portal_patcher.__enter__()
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    call = {
        "calls": [],
        "verification": {},
        "mounted": threading.Event(),
        "result": {},
        "stdout": io.StringIO(),
        "stderr": io.StringIO(),
        "published_page": None,
    }

    def platform_runner(arguments):
        call["calls"].append(arguments)
        if arguments[2] == "mount":
            connection = UnixSocketHTTPConnection(arguments[5], timeout=1)
            try:
                connection.request("GET", "/", headers={"Connection": "close"})
                response = connection.getresponse()
                call["verification"]["status"] = response.status
                call["verification"]["page"] = response.read()
            except (OSError, http.client.HTTPException) as error:
                call["verification"]["error"] = str(error)
            finally:
                connection.close()
                call["mounted"].set()
            valid_page = call["verification"].get(
                "status"
            ) == 200 and b"decision-data" in call["verification"].get("page", b"")
            return subprocess.CompletedProcess(
                arguments,
                0 if valid_page else 1,
                "https://portal.example/decision-test/\n" if valid_page else "",
                "" if valid_page else "mount verification failed\n",
            )
        if arguments[2] == "publish":
            call["published_page"] = (
                Path(arguments[4]) / "index.html"
            ).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    command = [
        "ask",
        "--decisions",
        str(decisions_path),
        "--name",
        "decision-test",
        "--answers",
        str(answers_path),
    ]
    call["thread"] = threading.Thread(
        target=lambda: call["result"].update(
            {
                "code": decision_page.main(
                    command,
                    platform_runner,
                    call["stdout"],
                    call["stderr"],
                )
            }
        ),
        daemon=True,
    )
    call["thread"].start()
    try:
        yield call
    finally:
        mount = next(
            (entry for entry in call["calls"] if entry[2] == "mount"), None
        )
        if call["thread"].is_alive() and mount is not None:
            try:
                default_answers = {
                    item["id"]: {"option": item["recommended"], "note": ""}
                    for group in decisions["groups"]
                    for item in group["items"]
                }
                request_socket(
                    mount[5],
                    "POST",
                    "/submit",
                    {"answers": default_answers},
                )
            except (OSError, http.client.HTTPException):
                pass
        call["thread"].join(5)
        portal_patcher.__exit__(None, None, None)


@contextmanager
def background_decision_page(decisions, answers, call_log):
    child_code = """
import json
import subprocess
import sys
sys.path.insert(0, sys.argv[1])
import decision_page

def run(arguments):
    try:
        with open(sys.argv[5], encoding="utf-8") as source:
            calls = json.load(source)
    except FileNotFoundError:
        calls = []
    calls.append(arguments)
    with open(sys.argv[5], "w", encoding="utf-8") as output:
        json.dump(calls, output)
    link = "https://portal.example/decision-test/" if arguments[2] == "mount" else ""
    return subprocess.CompletedProcess(arguments, 0, link, "")

arguments = ["ask", "--decisions", sys.argv[2], "--name", sys.argv[3],
             "--answers", sys.argv[4]]
raise SystemExit(decision_page.main(arguments, run, sys.stdout, sys.stderr))
"""
    process = subprocess.Popen(
        [
            "python3",
            "-B",
            "-c",
            child_code,
            str(DECISION_PAGE_DIRECTORY),
            str(decisions),
            "decision-test",
            str(answers),
            str(call_log),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        calls = []
        while time.monotonic() < deadline:
            if call_log.exists():
                calls = json.loads(call_log.read_text(encoding="utf-8"))
                if calls:
                    break
            if process.poll() is not None:
                break
            time.sleep(0.02)
        yield process, calls
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.communicate(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


class DecisionPageCommandTest(unittest.TestCase):
    def test_decisions_use_the_shared_json_shape_checker(self):
        with patch.object(
            decision_page,
            "check_value",
            wraps=decision_page.check_value,
        ) as shared_check:
            decision_page.validate_decisions(valid_decisions())

        self.assertTrue(shared_check.called)

    def test_decision_list_with_duplicate_json_key_exits_two(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            encoded = json.dumps(valid_decisions())
            encoded = encoded.replace(
                '"title": "Cleanup decisions"',
                '"title": "First title", "title": "Cleanup decisions"',
                1,
            )
            decisions.write_text(encoded, encoding="utf-8")
            code, _, stderr = run_ask(
                decisions,
                answers,
                lambda arguments: self.fail(f"不应启动平台：{arguments}"),
            )

        self.assertEqual(code, 2, stderr)
        self.assertIn("重复键：title", stderr)

    def test_decision_schema_errors_name_the_invalid_location(self):
        extra_top_level = valid_decisions()
        extra_top_level["legacy"] = True
        extra_group_field = valid_decisions()
        extra_group_field["groups"][0]["legacy"] = True
        empty_group = valid_decisions()
        empty_group["groups"][0]["items"] = []
        extra_item_field = valid_decisions()
        extra_item_field["groups"][0]["items"][0]["legacy"] = True
        duplicate_id = valid_decisions()
        duplicate_id["groups"].append(copy.deepcopy(duplicate_id["groups"][0]))
        too_few_options = valid_decisions()
        too_few_options["groups"][0]["items"][0]["options"] = ["Keep"]
        duplicate_options = valid_decisions()
        duplicate_options["groups"][0]["items"][0]["options"] = ["Keep", "Keep"]
        invalid_recommendation = valid_decisions()
        invalid_recommendation["groups"][0]["items"][0]["recommended"] = "Maybe"
        cases = [
            (extra_top_level, "legacy"),
            (
                {
                    key: value
                    for key, value in valid_decisions().items()
                    if key != "title"
                },
                "title",
            ),
            (extra_group_field, "groups[0]"),
            (empty_group, "groups[0].items"),
            (extra_item_field, "groups[0].items[0]"),
            (duplicate_id, "A1"),
            (too_few_options, "options"),
            (duplicate_options, "options"),
            (invalid_recommendation, "recommended"),
            ({**valid_decisions(), "groups": []}, "groups"),
        ]

        for decisions_data, expected in cases:
            with self.subTest(expected=expected):
                with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
                    decisions = Path(temporary) / "decisions.json"
                    answers = Path(temporary) / "answers.json"
                    decisions.write_text(json.dumps(decisions_data), encoding="utf-8")
                    code, _, stderr = run_ask(
                        decisions,
                        answers,
                        lambda arguments: self.fail(f"不应启动平台：{arguments}"),
                    )
                self.assertEqual(code, 2, stderr)
                self.assertIn(expected, stderr)

    def test_existing_answers_exit_two_without_starting_platform(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")
            answers.write_text("keep", encoding="utf-8")
            code, _, stderr = run_ask(
                decisions,
                answers,
                lambda arguments: self.fail(f"不应启动平台：{arguments}"),
            )
            self.assertEqual(answers.read_text(encoding="utf-8"), "keep")

        self.assertEqual(code, 2, stderr)
        self.assertIn("已经存在", stderr)

    def test_existing_broken_answer_symlink_exits_two_without_mounting(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")
            answers.symlink_to(Path(temporary) / "missing-answer.json")
            calls = []

            def platform_runner(arguments):
                calls.append(arguments)
                return subprocess.CompletedProcess(arguments, 1, "", "mount failed")

            code, _, _ = run_ask(decisions, answers, platform_runner)

            self.assertEqual(code, 2)
            self.assertEqual(calls, [])

    def test_answer_save_preflight_rejects_unavailable_directory_and_hard_links(self):
        failures = ("missing-directory", "unwritable", "hard-links")
        for failure in failures:
            with self.subTest(failure=failure):
                with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
                    directory = Path(temporary)
                    decisions = directory / "decisions.json"
                    answers_parent = (
                        directory / "missing"
                        if failure == "missing-directory"
                        else directory
                    )
                    answers = answers_parent / "answers.json"
                    decisions.write_text(
                        json.dumps(valid_decisions()), encoding="utf-8"
                    )
                    calls = []

                    def platform_runner(arguments):
                        calls.append(arguments)
                        return subprocess.CompletedProcess(
                            arguments, 1, "", "mount failed"
                        )

                    if failure == "unwritable":
                        patcher = patch(
                            "decision_page.tempfile.NamedTemporaryFile",
                            side_effect=PermissionError("directory is not writable"),
                        )
                    elif failure == "hard-links":
                        patcher = patch(
                            "decision_page.os.link",
                            side_effect=OSError("hard links are unsupported"),
                        )
                    else:
                        patcher = nullcontext()
                    with patcher:
                        code, _, _ = run_ask(decisions, answers, platform_runner)

                    self.assertEqual(code, 2)
                    self.assertEqual(calls, [])
                    self.assertEqual(list(directory.glob(".answers.json.*")), [])

    def test_page_escapes_script_text_and_embeds_each_call_id(self):
        decisions = valid_decisions()
        decisions["intro"] = ["Before </script> after"]
        code = (
            "import json,sys; sys.path.insert(0,sys.argv[1]); "
            "from decision_page import render_decision_page; "
            "data=json.loads(sys.argv[2]); "
            "print(json.dumps([render_decision_page(data), "
            "render_decision_page(data)]))"
        )
        result = subprocess.run(
            [
                "python3",
                "-B",
                "-c",
                code,
                str(DECISION_PAGE_DIRECTORY),
                json.dumps(decisions, ensure_ascii=False),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        first_page, second_page = json.loads(result.stdout)
        first_data = re.search(
            r'<script id="decision-data" type="application/json">(.*?)</script>',
            first_page,
            re.DOTALL,
        )
        second_data = re.search(
            r'<script id="decision-data" type="application/json">(.*?)</script>',
            second_page,
            re.DOTALL,
        )
        self.assertIsNotNone(first_data)
        self.assertIsNotNone(second_data)
        self.assertIn(r"Before \u003c/script> after", first_data.group(1))
        first_call_id = json.loads(first_data.group(1))["call_id"]
        second_call_id = json.loads(second_data.group(1))["call_id"]
        self.assertTrue(first_call_id)
        self.assertNotEqual(first_call_id, second_call_id)

    def test_received_page_contains_a_machine_readable_marker_and_saved_answers(self):
        self.assertTrue(
            hasattr(decision_page, "render_received_page"),
            "决定页模块必须能生成静态已收到页",
        )
        answers = {"A1": {"option": "Remove", "note": "用户写的说明。"}}

        page = decision_page.render_received_page(valid_decisions(), answers)

        self.assertIn(
            '<meta name="decision-state" content="received">', page
        )
        embedded = re.search(
            r'<script id="decision-data" type="application/json">(.*?)</script>',
            page,
            re.DOTALL,
        )
        self.assertIsNotNone(embedded)
        page_data = json.loads(embedded.group(1))
        self.assertEqual(page_data["state"], "received")
        self.assertEqual(page_data["answers"], answers)

    def test_ask_serves_the_page_and_unmounts_after_answer(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            decisions_data = valid_decisions()
            decisions.write_text(json.dumps(decisions_data), encoding="utf-8")

            with running_ask(decisions, answers) as call:
                self.assertTrue(call["mounted"].wait(3), call["stderr"].getvalue())
                mount = call["calls"][0]
                self.assertEqual(
                    mount[:5],
                    [
                        "python3",
                        str(decision_page.PORTAL_SCRIPT),
                        "mount",
                        "decision-test",
                        "--app",
                    ],
                )
                socket_path = mount[5]
                status, page = request_socket(socket_path, "GET", "/", None)
                self.assertEqual(status, 200)
                self.assertIn(b"decision-data", page)
                self.assertEqual(call["verification"]["status"], 200)
                self.assertIn(b"Cleanup decisions", page)
                status, _ = request_socket(socket_path, "GET", "/index.html", None)
                self.assertEqual(status, 404)

                answers_data = {"A1": {"option": "Keep", "note": "用户的备注。"}}
                status, body = request_socket(
                    socket_path, "POST", "/submit", {"answers": answers_data}
                )
                self.assertEqual(status, 200, body)
                call["thread"].join(5)
                self.assertFalse(call["thread"].is_alive())
                self.assertEqual(call["result"]["code"], 0)
                self.assertEqual(
                    [entry[2] for entry in call["calls"]],
                    ["mount", "unmount", "publish"],
                )
                self.assertIn(
                    '<meta name="decision-state" content="received">',
                    call["published_page"],
                )
                self.assertIn("用户的备注。", call["published_page"])
                self.assertIn(
                    "https://portal.example/decision-test/", call["stdout"].getvalue()
                )
                self.assertEqual(
                    json.loads(answers.read_text(encoding="utf-8")),
                    {"title": "Cleanup decisions", "answers": answers_data},
                )
                self.assertFalse(Path(socket_path).exists())

    def test_duplicate_submissions_return_success_only_for_the_saved_answers(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            directory = Path(temporary)
            answers_path = directory / "answers.json"
            decisions = valid_decisions()
            with decision_receiver(directory, decisions, answers_path) as (
                socket_path,
                serving,
            ):
                submitted = {
                    "answers": {"A1": {"option": "Keep", "note": "原备注。"}}
                }
                status, body = request_socket(
                    socket_path, "POST", "/submit", submitted
                )
                self.assertEqual(status, 200, body)
                try:
                    same_status, same_body = request_socket(
                        socket_path,
                        "POST",
                        "/submit",
                        submitted,
                        timeout=1,
                    )
                    different_status, different_body = request_socket(
                        socket_path,
                        "POST",
                        "/submit",
                        {
                            "answers": {
                                "A1": {"option": "Keep", "note": "不同备注。"}
                            }
                        },
                        timeout=1,
                    )
                except (OSError, http.client.HTTPException) as error:
                    self.fail(f"已提交的服务没有处理重复提交：{error}")
                self.assertEqual(same_status, 200, same_body)
                self.assertEqual(different_status, 409, different_body)
                self.assertEqual(
                    json.loads(answers_path.read_text(encoding="utf-8"))["answers"],
                    submitted["answers"],
                )
                self.assertTrue(serving.is_alive())

    def test_ask_replaces_only_an_existing_static_received_page(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            directory = Path(temporary)
            portal_script = directory / "portal.py"
            decisions = directory / "decisions.json"
            answers = directory / "answers.json"
            decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")
            published = directory / "published" / "decision-test-old"
            published.mkdir(parents=True)
            (published / "index.html").write_text(
                '<meta name="decision-state" content="received">',
                encoding="utf-8",
            )
            site = directory / "sites" / "decision-test"
            site.parent.mkdir()
            site.symlink_to(published, target_is_directory=True)

            with running_ask(decisions, answers, portal_script) as call:
                self.assertTrue(call["mounted"].wait(3), call["stderr"].getvalue())
                self.assertEqual(
                    [entry[2] for entry in call["calls"][:2]],
                    ["unmount", "mount"],
                )
                socket_path = next(
                    entry[5] for entry in call["calls"] if entry[2] == "mount"
                )
                status, body = request_socket(
                    socket_path,
                    "POST",
                    "/submit",
                    {"answers": {"A1": {"option": "Keep", "note": ""}}},
                )
                self.assertEqual(status, 200, body)
                call["thread"].join(5)
                self.assertFalse(call["thread"].is_alive())
                self.assertEqual(call["result"]["code"], 0)
                self.assertEqual(
                    [entry[2] for entry in call["calls"]],
                    ["unmount", "mount", "unmount", "publish"],
                )

    def test_ask_does_not_remove_another_static_site_with_the_same_name(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            directory = Path(temporary)
            portal_script = directory / "portal.py"
            decisions = directory / "decisions.json"
            answers = directory / "answers.json"
            decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")
            published = directory / "published" / "decision-test-old"
            published.mkdir(parents=True)
            (published / "index.html").write_text("other page", encoding="utf-8")
            site = directory / "sites" / "decision-test"
            site.parent.mkdir()
            site.symlink_to(published, target_is_directory=True)
            calls = []

            def platform_runner(arguments):
                calls.append(arguments)
                return subprocess.CompletedProcess(
                    arguments,
                    1,
                    "",
                    "挂载名已有目录挂载",
                )

            with patch.object(decision_page, "PORTAL_SCRIPT", portal_script):
                code, _stdout, stderr = run_ask(
                    decisions, answers, platform_runner
                )

            self.assertEqual(code, 1, stderr)
            self.assertEqual([entry[2] for entry in calls], ["mount"])
            self.assertTrue(site.is_symlink())

    def test_ask_rejects_invalid_and_oversized_submissions(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")

            with running_ask(decisions, answers) as call:
                self.assertTrue(call["mounted"].wait(3), call["stderr"].getvalue())
                socket_path = call["calls"][0][5]
                invalid_answers = [
                    {},
                    {"A1": {"option": "Other", "note": ""}},
                    {"A1": {"option": "Keep", "note": None}},
                    {
                        "A1": {"option": "Keep", "note": ""},
                        "A2": {"option": "Keep", "note": ""},
                    },
                ]
                for payload in invalid_answers:
                    with self.subTest(answers=payload):
                        status, _ = request_socket(
                            socket_path, "POST", "/submit", {"answers": payload}
                        )
                        self.assertEqual(status, 400)
                        self.assertFalse(answers.exists())

                status, _ = request_raw_socket(
                    socket_path, raw_body=None, content_length=204801
                )
                self.assertEqual(status, 400)
                status, _ = request_raw_socket(
                    socket_path, raw_body=None, content_length="9" * 5000
                )
                self.assertEqual(status, 400)
                self.assertFalse(answers.exists())

                answers_data = {"A1": {"option": "Keep", "note": ""}}
                status, body = request_socket(
                    socket_path, "POST", "/submit", {"answers": answers_data}
                )
                self.assertEqual(status, 200, body)
                call["thread"].join(5)
                self.assertFalse(call["thread"].is_alive())
                self.assertEqual(call["result"]["code"], 0)
                self.assertEqual(
                    json.loads(answers.read_text(encoding="utf-8"))["answers"],
                    answers_data,
                )

    def test_failed_answer_write_leaves_no_file_and_service_accepts_retry(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            directory = Path(temporary)
            answers_path = Path(temporary) / "answers.json"
            decisions = valid_decisions()
            with decision_receiver(directory, decisions, answers_path) as (
                socket_path,
                serving,
            ):
                valid_payload = {"answers": {"A1": {"option": "Keep", "note": ""}}}

                def fail_after_partial_write(document, output, ensure_ascii, indent):
                    output.write("{")
                    raise OSError("simulated full disk")

                with patch.object(
                    decision_page.json, "dump", side_effect=fail_after_partial_write
                ):
                    status, body = request_socket(
                        str(socket_path), "POST", "/submit", valid_payload
                    )
                self.assertEqual(status, 500, body)
                self.assertFalse(answers_path.exists())
                self.assertEqual(list(Path(temporary).glob("*.tmp")), [])

                status, body = request_socket(
                    str(socket_path), "POST", "/submit", valid_payload
                )
                self.assertEqual(status, 200, body)
                self.assertTrue(serving.is_alive())
                self.assertEqual(
                    json.loads(answers_path.read_text(encoding="utf-8")),
                    {"title": decisions["title"], "answers": valid_payload["answers"]},
                )

    def test_answer_with_duplicate_json_key_returns_400_without_writing(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            directory = Path(temporary)
            answers_path = Path(temporary) / "answers.json"
            decisions = valid_decisions()
            with decision_receiver(directory, decisions, answers_path) as (
                socket_path,
                serving,
            ):
                raw_answer = (
                    '{"answers":{"A1":{"option":"Keep","option":"Remove","note":""}}}'
                )
                with patch.object(decision_page.DecisionHTTPServer, "handle_error"):
                    status, body = request_raw_socket(
                        str(socket_path),
                        raw_answer,
                        content_length=None,
                    )
                self.assertEqual(status, 400, body)
                self.assertIn("重复键：option", body.decode("utf-8"))
                self.assertFalse(answers_path.exists())

    def test_incomplete_request_line_times_out_before_next_submission(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            answers = Path(temporary) / "answers.json"
            decisions = valid_decisions()
            with decision_receiver(Path(temporary), decisions, answers) as (
                socket_path,
                serving,
            ):
                partial = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                complete = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    partial.connect(socket_path)
                    partial.sendall(b"POST /sub")
                    time.sleep(0.1)

                    payload = json.dumps(
                        {"answers": {"A1": {"option": "Keep", "note": ""}}}
                    ).encode("utf-8")
                    complete.settimeout(12)
                    complete.connect(socket_path)
                    complete.sendall(
                        b"POST /submit HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n"
                        b"Content-Type: application/json\r\nContent-Length: "
                        + str(len(payload)).encode("ascii")
                        + b"\r\n\r\n"
                        + payload
                    )
                    response = bytearray()
                    try:
                        while True:
                            chunk = complete.recv(4096)
                            if not chunk:
                                break
                            response.extend(chunk)
                    except socket.timeout:
                        pass

                    self.assertIn(b"HTTP/1.0 200 OK", response)
                    self.assertTrue(answers.exists())
                    self.assertTrue(serving.is_alive())
                finally:
                    partial.shutdown(socket.SHUT_WR)
                    partial.close()
                    complete.close()

    def test_slow_page_reader_receives_complete_response_after_timeout_period(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            with large_page_request(
                Path(temporary), Path(temporary) / "answers.json"
            ) as (
                _,
                client,
            ):
                response = http.client.HTTPResponse(client)
                response.begin()
                content_length = int(response.getheader("Content-Length"))
                body = bytearray()
                started = time.monotonic()
                while len(body) < content_length:
                    chunk = response.read(min(16 * 1024, content_length - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    time.sleep(0.01)
                elapsed = time.monotonic() - started

        self.assertGreater(content_length, 2 * 1024 * 1024)
        self.assertGreater(elapsed, 0.5)
        self.assertEqual(len(body), content_length)

    def test_idle_page_reader_disconnects_and_server_accepts_next_request(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            with large_page_request(
                Path(temporary), Path(temporary) / "answers.json"
            ) as (
                socket_path,
                client,
            ):
                time.sleep(0.8)
                client.settimeout(2)
                response_bytes = bytearray()
                disconnected = False
                try:
                    while True:
                        chunk = client.recv(16 * 1024)
                        if not chunk:
                            disconnected = True
                            break
                        response_bytes.extend(chunk)
                except socket.timeout:
                    pass

                response_head, response_body = bytes(response_bytes).split(
                    b"\r\n\r\n", 1
                )
                content_length = int(
                    next(
                        line.split(b":", 1)[1].strip()
                        for line in response_head.split(b"\r\n")
                        if line.lower().startswith(b"content-length:")
                    )
                )
                next_status, next_body = request_socket(socket_path, "GET", "/", None)

        self.assertTrue(disconnected, "停读的客户端在超时后仍未断开")
        self.assertIn(b"200 OK", response_head)
        self.assertLess(len(response_body), content_length)
        self.assertEqual(next_status, 200)
        self.assertIn(b"decision-data", next_body)

    def test_mount_failure_cleans_only_a_mount_created_by_this_call(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")
            portal = Path(temporary) / "portal.py"
            app_file = portal.parent / "apps" / "decision-test.caddy"

            with self.subTest(result="partial mount from this call"):
                calls = []

                def create_then_fail(arguments):
                    calls.append(arguments)
                    if arguments[2] == "mount":
                        app_file.parent.mkdir(parents=True, exist_ok=True)
                        app_file.write_text(
                            f"socket={arguments[-1]}\n", encoding="utf-8"
                        )
                        return subprocess.CompletedProcess(
                            arguments, 1, "", "mount failed\n"
                        )
                    app_file.unlink(missing_ok=True)
                    return subprocess.CompletedProcess(arguments, 0, "", "")

                with patch.object(decision_page, "PORTAL_SCRIPT", portal):
                    code, _, _ = run_ask(decisions, answers, create_then_fail)

                self.assertEqual(code, 1)
                self.assertEqual([call[2] for call in calls], ["mount", "unmount"])
                self.assertFalse(app_file.exists())

            with self.subTest(result="preexisting mount from another call"):
                app_file.parent.mkdir(parents=True, exist_ok=True)
                app_file.write_text("socket=/tmp/another-call/s\n", encoding="utf-8")
                calls = []

                def fail_without_replacing(arguments):
                    calls.append(arguments)
                    return subprocess.CompletedProcess(
                        arguments, 1, "", "name is occupied\n"
                    )

                with patch.object(decision_page, "PORTAL_SCRIPT", portal):
                    code, _, _ = run_ask(decisions, answers, fail_without_replacing)

                self.assertEqual(code, 1)
                self.assertEqual([call[2] for call in calls], ["mount"])
                self.assertEqual(
                    app_file.read_text(encoding="utf-8"),
                    "socket=/tmp/another-call/s\n",
                )
                self.assertFalse(answers.exists())

    def test_term_unmounts_site_and_removes_socket_without_an_answer(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            call_log = Path(temporary) / "calls.json"
            decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")

            with background_decision_page(decisions, answers, call_log) as (
                process,
                calls,
            ):
                self.assertTrue(calls, "mount was not attempted")
                socket_path = calls[0][5]
                self.assertTrue(Path(socket_path).exists())
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=12)
                saved_calls = json.loads(call_log.read_text(encoding="utf-8"))

            self.assertEqual(process.returncode, 1, stdout + stderr)
            self.assertEqual([call[2] for call in saved_calls], ["mount", "unmount"])
            self.assertFalse(answers.exists())
            self.assertFalse(Path(socket_path).exists())

    def test_term_interrupts_a_stalled_request_before_idle_timeout(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            call_log = Path(temporary) / "calls.json"
            decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")

            with background_decision_page(decisions, answers, call_log) as (
                process,
                calls,
            ):
                self.assertTrue(calls, "mount was not attempted")
                socket_path = calls[0][5]
                partial = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                partial.connect(socket_path)
                partial.sendall(b"POST /sub")
                time.sleep(0.1)
                started = time.monotonic()
                process.send_signal(signal.SIGTERM)
                exited_on_timeout = True
                try:
                    stdout, stderr = process.communicate(timeout=12)
                except subprocess.TimeoutExpired:
                    exited_on_timeout = False
                    partial.close()
                    stdout, stderr = process.communicate(timeout=5)
                elapsed = time.monotonic() - started
                partial.close()
                saved_calls = json.loads(call_log.read_text(encoding="utf-8"))

            self.assertEqual(process.returncode, 1, stdout + stderr)
            self.assertEqual([call[2] for call in saved_calls], ["mount", "unmount"])
            self.assertFalse(answers.exists())
            self.assertFalse(Path(socket_path).exists())
            self.assertTrue(
                exited_on_timeout, f"TERM exit waited too long: {elapsed:.2f}s"
            )
            self.assertLess(elapsed, 11.5, f"TERM exit waited too long: {elapsed:.2f}s")
