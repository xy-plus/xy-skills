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


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "xy-cleanup"))

import decision_page  # noqa: E402


def valid_decisions():
    return {
        "title": "Cleanup decisions",
        "intro": ["Read every item."],
        "groups": [{
            "title": "Review",
            "notes": [],
            "items": [{
                "id": "A1",
                "title": "Feature",
                "lines": ["Keep this feature."],
                "options": ["Keep", "Remove"],
                "recommended": "Keep",
            }],
        }],
        "footnotes": [],
    }


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def request_socket(socket_path, method, path, data):
    body = None if data is None else json.dumps(data).encode("utf-8")
    headers = {"Connection": "close"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    connection = UnixSocketHTTPConnection(socket_path, timeout=None)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def request_raw_socket(socket_path, raw_body, content_length):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(socket_path)
    body = b"" if raw_body is None else raw_body.encode("utf-8")
    length = len(body) if content_length is None else content_length
    headers = (
        b"POST /submit HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n"
        b"Content-Type: application/json\r\nContent-Length: "
        + str(length).encode("ascii") + b"\r\n\r\n"
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
        socket_path, decision_page.render_decision_page(decisions), decisions, answers,
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
        server.server_close()
        socket_path.unlink(missing_ok=True)
        serving.join(2)


class DecisionPageCommandTest(unittest.TestCase):
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
            stderr = io.StringIO()
            code = decision_page.main(
                ["ask", "--decisions", str(decisions), "--name", "decision-test",
                 "--answers", str(answers)],
                lambda arguments: self.fail(f"不应启动平台：{arguments}"),
                io.StringIO(),
                stderr,
            )

        self.assertEqual(code, 2, stderr.getvalue())
        self.assertIn("重复键：title", stderr.getvalue())

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
            ({key: value for key, value in valid_decisions().items() if key != "title"}, "title"),
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
                    stderr = io.StringIO()
                    code = decision_page.main(
                        ["ask", "--decisions", str(decisions), "--name", "decision-test",
                         "--answers", str(answers)],
                        lambda arguments: self.fail(f"不应启动平台：{arguments}"),
                        io.StringIO(),
                        stderr,
                    )
                self.assertEqual(code, 2, stderr.getvalue())
                self.assertIn(expected, stderr.getvalue())

    def test_existing_answers_exit_two_without_starting_platform(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")
            answers.write_text("keep", encoding="utf-8")
            stderr = io.StringIO()
            code = decision_page.main(
                ["ask", "--decisions", str(decisions), "--name", "decision-test",
                 "--answers", str(answers)],
                lambda arguments: self.fail(f"不应启动平台：{arguments}"),
                io.StringIO(),
                stderr,
            )
            self.assertEqual(answers.read_text(encoding="utf-8"), "keep")

        self.assertEqual(code, 2, stderr.getvalue())
        self.assertIn("已经存在", stderr.getvalue())

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

            code = decision_page.main(
                ["ask", "--decisions", str(decisions), "--name", "decision-test",
                 "--answers", str(answers)],
                platform_runner,
                io.StringIO(),
                io.StringIO(),
            )

            self.assertEqual(code, 2)
            self.assertEqual(calls, [])

    def test_answer_save_preflight_rejects_unavailable_directory_and_hard_links(self):
        failures = ("missing-directory", "unwritable", "hard-links")
        for failure in failures:
            with self.subTest(failure=failure):
                with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
                    directory = Path(temporary)
                    decisions = directory / "decisions.json"
                    answers_parent = directory / "missing" if failure == "missing-directory" else directory
                    answers = answers_parent / "answers.json"
                    decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")
                    calls = []

                    def platform_runner(arguments):
                        calls.append(arguments)
                        return subprocess.CompletedProcess(arguments, 1, "", "mount failed")

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
                        code = decision_page.main(
                            ["ask", "--decisions", str(decisions), "--name", "decision-test",
                             "--answers", str(answers)],
                            platform_runner,
                            io.StringIO(),
                            io.StringIO(),
                        )

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
            ["python3", "-B", "-c", code, str(ROOT / "xy-cleanup"),
             json.dumps(decisions, ensure_ascii=False)],
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

    def test_ask_mounts_receives_one_valid_answer_and_unmounts(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions_data = valid_decisions()
            decisions_data["title"] = "Cleanup decisions"
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            decisions.write_text(json.dumps(decisions_data), encoding="utf-8")
            calls = []
            verification = {}
            mounted = threading.Event()
            result = {}
            stdout = io.StringIO()
            stderr = io.StringIO()

            def platform_runner(arguments):
                calls.append(arguments)
                if arguments[2] == "mount":
                    connection = UnixSocketHTTPConnection(arguments[5], timeout=1)
                    try:
                        connection.request("GET", "/", headers={"Connection": "close"})
                        response = connection.getresponse()
                        verification["status"] = response.status
                        verification["page"] = response.read()
                    except (OSError, http.client.HTTPException) as error:
                        verification["error"] = str(error)
                    finally:
                        connection.close()
                        mounted.set()

                    if (verification.get("status") != 200
                            or b"decision-data" not in verification.get("page", b"")):
                        return subprocess.CompletedProcess(
                            arguments, 1, "", "mount verification failed\n"
                        )
                    return subprocess.CompletedProcess(
                        arguments, 0, "https://portal.example/decision-test/\n", ""
                    )
                return subprocess.CompletedProcess(arguments, 0, "", "")

            arguments = ["ask", "--decisions", str(decisions), "--name", "decision-test",
                         "--answers", str(answers)]
            thread = threading.Thread(
                target=lambda: result.setdefault(
                    "code", decision_page.main(arguments, platform_runner, stdout, stderr)
                ),
                daemon=True,
            )
            thread.start()
            self.assertTrue(mounted.wait(3), stderr.getvalue())
            mount = calls[0]
            self.assertEqual(
                mount,
                ["python3", str(decision_page.PORTAL_SCRIPT), "mount", "decision-test",
                 "--app", mount[5], "--login", "portal"],
            )
            socket_path = mount[5]

            status, page = request_socket(socket_path, "GET", "/", None)
            self.assertEqual(status, 200)
            self.assertIn(b"decision-data", page)
            self.assertEqual(verification.get("status"), 200, verification.get("error"))
            self.assertIn(b"decision-data", verification.get("page", b""))
            self.assertIn(b"Cleanup decisions", page)

            status, body = request_socket(socket_path, "GET", "/index.html", None)
            self.assertEqual(status, 404, body)

            valid_answers = {"A1": {"option": "Keep", "note": ""}}
            invalid_answers = [
                {},
                {"A1": {"option": "Other", "note": ""}},
                {"A1": {"option": "Keep", "note": None}},
                {"A1": {"option": "Keep", "note": ""}, "A2": {"option": "Keep", "note": ""}},
            ]
            for answers_data in invalid_answers:
                with self.subTest(answers=answers_data):
                    status, body = request_socket(
                        socket_path, "POST", "/submit",
                        {"answers": answers_data},
                    )
                    self.assertEqual(status, 400, body)
                    self.assertFalse(answers.exists())

            status, body = request_raw_socket(
                socket_path, raw_body=None, content_length=204801,
            )
            self.assertEqual(status, 400, body)
            self.assertFalse(answers.exists())

            status, body = request_raw_socket(
                socket_path, raw_body=None, content_length="9" * 5000,
            )
            self.assertEqual(status, 400, body)
            self.assertFalse(answers.exists())

            status, body = request_socket(
                socket_path, "POST", "/submit",
                {"answers": valid_answers},
            )
            self.assertEqual(status, 200, body)
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result["code"], 0)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1], ["python3", str(decision_page.PORTAL_SCRIPT), "unmount", "decision-test"])
            self.assertIn("https://portal.example/decision-test/", stdout.getvalue())
            self.assertEqual(
                json.loads(answers.read_text(encoding="utf-8")),
                {"title": "Cleanup decisions", "answers": valid_answers},
            )
            self.assertFalse(Path(socket_path).exists())

    def test_failed_answer_write_leaves_no_file_and_service_accepts_retry(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            directory = Path(temporary)
            answers_path = Path(temporary) / "answers.json"
            decisions = valid_decisions()
            with decision_receiver(directory, decisions, answers_path) as (socket_path, serving):
                valid_payload = {"answers": {"A1": {"option": "Keep", "note": ""}}}

                def fail_after_partial_write(document, output, ensure_ascii, indent):
                    output.write("{")
                    raise OSError("simulated full disk")

                with patch.object(decision_page.json, "dump", side_effect=fail_after_partial_write):
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
                serving.join(5)
                self.assertFalse(serving.is_alive())
                self.assertEqual(
                    json.loads(answers_path.read_text(encoding="utf-8")),
                    {"title": decisions["title"], "answers": valid_payload["answers"]},
                )

    def test_answer_with_duplicate_json_key_returns_400_without_writing(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            directory = Path(temporary)
            answers_path = Path(temporary) / "answers.json"
            decisions = valid_decisions()
            with decision_receiver(directory, decisions, answers_path) as (socket_path, serving):
                raw_answer = (
                    '{"answers":{"A1":{"option":"Keep","option":"Remove",'
                    '"note":""}}}'
                )
                with patch.object(decision_page.DecisionHTTPServer, "handle_error"):
                    status, body = request_raw_socket(
                        str(socket_path), raw_answer, content_length=None,
                    )
                self.assertEqual(status, 400, body)
                self.assertIn("重复键：option", body.decode("utf-8"))
                self.assertFalse(answers_path.exists())

    def test_incomplete_request_line_times_out_before_next_submission(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            socket_path = Path(temporary) / "s"
            answers = Path(temporary) / "answers.json"
            decisions = valid_decisions()
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
            partial = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            complete = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                partial.connect(str(socket_path))
                partial.sendall(b"POST /sub")
                time.sleep(0.1)

                payload = json.dumps(
                    {"answers": {"A1": {"option": "Keep", "note": ""}}}
                ).encode("utf-8")
                complete.settimeout(12)
                complete.connect(str(socket_path))
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
            finally:
                partial.shutdown(socket.SHUT_WR)
                stop_event.set()
                serving.join(3)
                partial.close()
                complete.close()
                server.server_close()
                socket_path.unlink(missing_ok=True)
            self.assertFalse(serving.is_alive())

    def test_mount_failure_stops_service_without_unmounting(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")
            calls = []

            def platform_runner(arguments):
                calls.append(arguments)
                return subprocess.CompletedProcess(arguments, 1, "", "name is occupied\n")

            code = decision_page.main(
                ["ask", "--decisions", str(decisions), "--name", "decision-test",
                 "--answers", str(answers)],
                platform_runner,
                io.StringIO(),
                io.StringIO(),
            )

            self.assertEqual(code, 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][2:5], ["mount", "decision-test", "--app"])
            self.assertFalse(Path(calls[0][5]).exists())
            self.assertFalse(answers.exists())

    def test_term_unmounts_its_site_and_removes_service_without_answer(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            call_log = Path(temporary) / "calls.json"
            decisions.write_text(json.dumps(valid_decisions()), encoding="utf-8")
            code = '''
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
'''
            process = subprocess.Popen(
                ["python3", "-B", "-c", code, str(ROOT / "xy-cleanup"),
                 str(decisions), "decision-test", str(answers), str(call_log)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            partial = None
            socket_path = None
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
                self.assertTrue(calls, "mount was not attempted")
                socket_path = calls[0][5]
                self.assertTrue(Path(socket_path).exists())
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
                calls = json.loads(call_log.read_text(encoding="utf-8"))
                self.assertEqual(process.returncode, 1, stdout + stderr)
                self.assertEqual([call[2] for call in calls], ["mount", "unmount"])
                self.assertFalse(answers.exists())
                self.assertFalse(Path(socket_path).exists())
                self.assertTrue(exited_on_timeout, f"TERM exit waited too long: {elapsed:.2f}s")
                self.assertLess(elapsed, 11.5, f"TERM exit waited too long: {elapsed:.2f}s")
            finally:
                if partial is not None:
                    partial.close()
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.communicate(timeout=12)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=5)
                if socket_path is not None:
                    Path(socket_path).unlink(missing_ok=True)

    def test_each_object_level_requires_exact_fields(self):
        with_footnote = valid_decisions()
        with_footnote["footnotes"] = [{"title": "Keep in mind", "lines": ["No action needed."]}]
        cases = []

        missing_top = valid_decisions()
        del missing_top["intro"]
        cases.append((missing_top, "intro"))
        missing_group = valid_decisions()
        del missing_group["groups"][0]["notes"]
        cases.append((missing_group, "notes"))
        missing_item = valid_decisions()
        del missing_item["groups"][0]["items"][0]["lines"]
        cases.append((missing_item, "lines"))
        missing_footnote = copy.deepcopy(with_footnote)
        del missing_footnote["footnotes"][0]["title"]
        cases.append((missing_footnote, "title"))
        extra_footnote = copy.deepcopy(with_footnote)
        extra_footnote["footnotes"][0]["legacy"] = True
        cases.append((extra_footnote, "legacy"))

        for decisions, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError) as context:
                    decision_page.validate_decisions(decisions)
                self.assertIn(expected, str(context.exception))

    def test_text_fields_are_nonempty_strings_at_every_level(self):
        cases = []
        for field, value, expected in (
            ("title", " ", "title"),
            ("intro", [""], "intro[0]"),
        ):
            decisions = valid_decisions()
            decisions[field] = value
            cases.append((decisions, expected))
        for section, field, value, expected in (
            ("group", "title", "", "groups[0].title"),
            ("group", "notes", [" "], "groups[0].notes[0]"),
            ("item", "id", "", "groups[0].items[0].id"),
            ("item", "title", "", "groups[0].items[0].title"),
            ("item", "lines", [""], "groups[0].items[0].lines[0]"),
            ("item", "options", ["Keep", " "], "groups[0].items[0].options[1]"),
            ("item", "recommended", " ", "groups[0].items[0].recommended"),
            ("footnote", "title", "", "footnotes[0].title"),
            ("footnote", "lines", [""], "footnotes[0].lines[0]"),
        ):
            decisions = valid_decisions()
            if section == "group":
                decisions["groups"][0][field] = value
            elif section == "item":
                decisions["groups"][0]["items"][0][field] = value
            else:
                decisions["footnotes"] = [{"title": "Keep in mind", "lines": ["No action needed."]}]
                decisions["footnotes"][0][field] = value
            cases.append((decisions, expected))

        for decisions, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError) as context:
                    decision_page.validate_decisions(decisions)
                self.assertIn(expected, str(context.exception))

    def test_arrays_must_have_their_documented_types(self):
        cases = []
        for key in ("intro", "groups", "footnotes"):
            decisions = valid_decisions()
            decisions[key] = "not an array"
            cases.append((decisions, key))
        invalid_group = valid_decisions()
        invalid_group["groups"][0]["notes"] = "not an array"
        cases.append((invalid_group, "notes"))
        invalid_items = valid_decisions()
        invalid_items["groups"][0]["items"] = "not an array"
        cases.append((invalid_items, "items"))
        invalid_lines = valid_decisions()
        invalid_lines["groups"][0]["items"][0]["lines"] = "not an array"
        cases.append((invalid_lines, "lines"))
        invalid_options = valid_decisions()
        invalid_options["groups"][0]["items"][0]["options"] = "not an array"
        cases.append((invalid_options, "options"))

        for decisions, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError) as context:
                    decision_page.validate_decisions(decisions)
                self.assertIn(expected, str(context.exception))
