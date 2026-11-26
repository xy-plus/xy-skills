"""Test the static receipt page and reask behavior."""

import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_decision_page as support


decision_page = support.decision_page


class DecisionReceiptTest(unittest.TestCase):
    def test_received_page_contains_marker_and_saved_answers(self):
        answers = {"A1": {"option": "Remove", "note": "用户写的说明。"}}

        page = decision_page.render_received_page(support.valid_decisions(), answers)

        self.assertIn(
            '<meta name="decision-state" content="received">', page
        )
        embedded = support.re.search(
            r'<script id="decision-data" type="application/json">(.*?)</script>',
            page,
            support.re.DOTALL,
        )
        self.assertIsNotNone(embedded)
        page_data = json.loads(embedded.group(1))
        self.assertEqual(page_data["answers"], answers)

    def test_ask_serves_the_page_and_publishes_a_static_receipt(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            decisions = Path(temporary) / "decisions.json"
            answers = Path(temporary) / "answers.json"
            decisions_data = support.valid_decisions()
            decisions.write_text(json.dumps(decisions_data), encoding="utf-8")

            with support.running_ask(decisions, answers) as call:
                self.assertTrue(call["mounted"].wait(3), call["stderr"].getvalue())
                mount = call["calls"][0]
                self.assertEqual(mount[2], "mount")
                socket_path = mount[5]
                status, page = support.request_socket(socket_path, "GET", "/", None)
                self.assertEqual(status, 200)
                self.assertIn(b"decision-data", page)
                self.assertEqual(call["verification"]["status"], 200)
                self.assertIn(b"Cleanup decisions", page)
                status, _ = support.request_socket(
                    socket_path, "GET", "/index.html", None
                )
                self.assertEqual(status, 404)

                answers_data = {"A1": {"option": "Keep", "note": "用户的备注。"}}
                support.submit_ask_answer(
                    self,
                    call,
                    socket_path,
                    answers_data,
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

    def test_duplicate_submissions_only_succeed_for_saved_answers(self):
        with support.temporary_decision_receiver() as (
            _directory,
            _decisions,
            answers_path,
            socket_path,
            serving,
        ):
            submitted = {"answers": {"A1": {"option": "Keep", "note": "原备注。"}}}
            status, body = support.request_socket(
                socket_path, "POST", "/submit", submitted
            )
            self.assertEqual(status, 200, body)
            try:
                same_status, same_body = support.request_socket(
                    socket_path,
                    "POST",
                    "/submit",
                    submitted,
                    timeout=1,
                )
                different_status, different_body = support.request_socket(
                    socket_path,
                    "POST",
                    "/submit",
                    {"answers": {"A1": {"option": "Keep", "note": "不同备注。"}}},
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
            portal_script, decisions, answers, _site = support.static_site_ask_fixture(
                directory,
                '<meta name="decision-state" content="received">',
            )

            with support.running_ask(decisions, answers, portal_script) as call:
                self.assertTrue(call["mounted"].wait(3), call["stderr"].getvalue())
                self.assertEqual(
                    [entry[2] for entry in call["calls"][:2]], ["unmount", "mount"]
                )
                socket_path = next(
                    entry[5] for entry in call["calls"] if entry[2] == "mount"
                )
                support.submit_ask_answer(
                    self,
                    call,
                    socket_path,
                    {"A1": {"option": "Keep", "note": ""}},
                    ["unmount", "mount", "unmount", "publish"],
                )

    def test_ask_does_not_remove_another_static_site_with_the_same_name(self):
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            directory = Path(temporary)
            portal_script, decisions, answers, site = support.static_site_ask_fixture(
                directory, "other page"
            )
            calls = []

            def reject_mount(arguments):
                calls.append(arguments)
                return subprocess.CompletedProcess(arguments, 1, "", "挂载名已有目录挂载")

            with patch.object(decision_page, "PORTAL_SCRIPT", portal_script):
                code, _stdout, stderr = support.run_ask(decisions, answers, reject_mount)

            self.assertEqual(code, 1, stderr)
            self.assertEqual([entry[2] for entry in calls], ["mount"])
            self.assertTrue(site.is_symlink())
