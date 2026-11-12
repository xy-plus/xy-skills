"""Exercise the decision page in Chromium through a local portal bridge."""
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "xy-cleanup"))

import decision_page  # noqa: E402


def browser_decisions():
    return {
        "title": "Cleanup decisions",
        "intro": ["Review every item."],
        "groups": [{
            "title": "Repository",
            "notes": ["Choose the option that fits this item."],
            "items": [
                {
                    "id": "A1",
                    "title": "Keep the feature",
                    "lines": ["What is needed: clear behavior."],
                    "options": ["Keep", "Remove"],
                    "recommended": "Keep",
                },
                {
                    "id": "B1",
                    "title": "Name the feature",
                    "lines": ["Why: a short name helps."],
                    "options": ["Use current name", "Rename"],
                    "recommended": "Use current name",
                },
                {
                    "id": "__proto__",
                    "title": "Prototype id",
                    "lines": ["This id is valid."],
                    "options": ["Keep prototype", "Change prototype"],
                    "recommended": "Keep prototype",
                },
                {
                    "id": "constructor",
                    "title": "Constructor id",
                    "lines": ["This id is valid too."],
                    "options": ["Keep constructor", "Change constructor"],
                    "recommended": "Keep constructor",
                },
            ],
        }],
        "footnotes": [{"title": "Already handled", "lines": ["No answer needed."]}],
    }


class DecisionPageBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *arguments):
        return

    def do_GET(self):
        if self.path != "/":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        body = self.server.page.encode("utf-8")
        self._send(200, body, "text/html; charset=utf-8")

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.received_payloads.append(json.loads(body))
        if self.server.fail_next_post:
            self.server.fail_next_post = False
            self._send(503, b'{"error":"temporary test failure"}', "application/json")
            return
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(self.server.receiver_path)
            request = (
                f"POST {self.path} HTTP/1.0\r\n"
                "Host: localhost\r\n"
                "Connection: close\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode("ascii") + body
            client.sendall(request)
            response = http.client.HTTPResponse(client)
            response.begin()
            status = response.status
            response_body = response.read()
            content_type = response.getheader("Content-Type", "application/json")
            self._send(status, response_body, content_type)
        finally:
            client.close()

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class DecisionPageBridge(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, page, receiver_path):
        self.page = page
        self.receiver_path = receiver_path
        self.fail_next_post = True
        self.received_payloads = []
        super().__init__(("127.0.0.1", 0), DecisionPageBridgeHandler)


class DecisionPageBrowserTest(unittest.TestCase):
    def test_decisions_persist_per_call_and_submit_from_a_phone_view(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable")
        decisions = browser_decisions()
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            socket_path = Path(temporary) / "s"
            answers_path = Path(temporary) / "answers.json"
            first_page = decision_page.render_decision_page(decisions)
            second_page = decision_page.render_decision_page(decisions)
            receiver = decision_page.DecisionHTTPServer(
                socket_path, first_page, decisions, answers_path
            )
            stop_event = threading.Event()
            receiver_thread = threading.Thread(
                target=receiver.serve_until_complete,
                args=(stop_event,),
                daemon=True,
            )
            bridge = DecisionPageBridge(first_page, str(socket_path))
            bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
            script_errors = []
            receiver_thread.start()
            bridge_thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=executable,
                        headless=True,
                    )
                    page = browser.new_page(viewport={"width": 390, "height": 844})
                    page.on("pageerror", lambda error: script_errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{bridge.server_port}/")

                    self.assertEqual(page.locator(".item").count(), 4)
                    first_item = page.locator('[data-item-id="A1"]')
                    second_item = page.locator('[data-item-id="B1"]')
                    prototype_item = page.locator('[data-item-id="__proto__"]')
                    constructor_item = page.locator('[data-item-id="constructor"]')
                    self.assertTrue(first_item.locator('input[value="Keep"]').is_checked())
                    self.assertTrue(first_item.locator(".suggested").count() == 1)
                    self.assertIn("写了字就以你写的为准", first_item.locator("textarea").get_attribute("placeholder"))
                    self.assertIn("共 4 条", page.locator("#status").inner_text())

                    first_item.locator('input[value="Remove"]').check()
                    first_item.locator("textarea").fill("Use the simpler behavior.")
                    second_item.locator("textarea").fill("Keep this note.")
                    prototype_item.locator('input[value="Change prototype"]').check()
                    prototype_item.locator("textarea").fill("Prototype answer.")
                    constructor_item.locator('input[value="Change constructor"]').check()
                    constructor_item.locator("textarea").fill("Constructor answer.")
                    self.assertTrue(first_item.evaluate("element => element.classList.contains('changed')"))
                    self.assertTrue(second_item.evaluate("element => element.classList.contains('changed')"))
                    self.assertTrue(prototype_item.evaluate("element => element.classList.contains('changed')"))
                    self.assertTrue(constructor_item.evaluate("element => element.classList.contains('changed')"))
                    self.assertIn("改了或写了 4 条", page.locator("#status").inner_text())
                    page.reload()
                    self.assertTrue(first_item.locator('input[value="Remove"]').is_checked())
                    self.assertEqual(first_item.locator("textarea").input_value(), "Use the simpler behavior.")
                    self.assertTrue(prototype_item.locator('input[value="Change prototype"]').is_checked())
                    self.assertEqual(prototype_item.locator("textarea").input_value(), "Prototype answer.")
                    self.assertTrue(constructor_item.locator('input[value="Change constructor"]').is_checked())
                    self.assertEqual(constructor_item.locator("textarea").input_value(), "Constructor answer.")

                    bridge.page = second_page
                    page.reload()
                    first_item = page.locator('[data-item-id="A1"]')
                    second_item = page.locator('[data-item-id="B1"]')
                    prototype_item = page.locator('[data-item-id="__proto__"]')
                    constructor_item = page.locator('[data-item-id="constructor"]')
                    self.assertTrue(first_item.locator('input[value="Keep"]').is_checked())
                    self.assertEqual(first_item.locator("textarea").input_value(), "")
                    self.assertTrue(second_item.locator('input[value="Use current name"]').is_checked())
                    self.assertTrue(prototype_item.locator('input[value="Keep prototype"]').is_checked())
                    self.assertTrue(constructor_item.locator('input[value="Keep constructor"]').is_checked())
                    self.assertEqual(prototype_item.locator("textarea").input_value(), "")
                    self.assertEqual(constructor_item.locator("textarea").input_value(), "")
                    self.assertFalse(first_item.evaluate("element => element.classList.contains('changed')"))

                    first_item.locator('input[value="Remove"]').check()
                    first_item.locator("textarea").fill("My written decision wins.")
                    prototype_item.locator('input[value="Change prototype"]').check()
                    prototype_item.locator("textarea").fill("Saved prototype answer.")
                    constructor_item.locator('input[value="Change constructor"]').check()
                    constructor_item.locator("textarea").fill("Saved constructor answer.")
                    self.assertLessEqual(
                        page.evaluate("document.documentElement.scrollWidth"),
                        page.evaluate("window.innerWidth"),
                    )
                    for selector in ("body", ".item .line", ".item textarea"):
                        font_size = page.locator(selector).first.evaluate(
                            "element => parseFloat(getComputedStyle(element).fontSize)"
                        )
                        self.assertGreaterEqual(font_size, 13, selector)

                    page.locator("#submit").click()
                    page.locator("#feedback").wait_for(state="visible")
                    self.assertIn("temporary test failure", page.locator("#feedback").inner_text())
                    self.assertTrue(first_item.locator('input[value="Remove"]').is_checked())
                    self.assertEqual(first_item.locator("textarea").input_value(), "My written decision wins.")
                    self.assertTrue(prototype_item.locator('input[value="Change prototype"]').is_checked())
                    self.assertEqual(prototype_item.locator("textarea").input_value(), "Saved prototype answer.")
                    self.assertTrue(constructor_item.locator('input[value="Change constructor"]').is_checked())
                    self.assertEqual(constructor_item.locator("textarea").input_value(), "Saved constructor answer.")
                    self.assertFalse(first_item.locator("textarea").is_disabled())

                    page.locator("#submit").click()
                    page.locator("#status").get_by_text("已收到").wait_for()
                    self.assertTrue(first_item.locator("textarea").is_disabled())
                    browser.close()

                receiver_thread.join(5)
                self.assertFalse(receiver_thread.is_alive())
                self.assertEqual(
                    json.loads(answers_path.read_text(encoding="utf-8")),
                    {
                        "title": "Cleanup decisions",
                        "answers": {
                            "A1": {"option": "Remove", "note": "My written decision wins."},
                            "B1": {"option": "Use current name", "note": ""},
                            "__proto__": {"option": "Change prototype", "note": "Saved prototype answer."},
                            "constructor": {"option": "Change constructor", "note": "Saved constructor answer."},
                        },
                    },
                )
                submitted = {
                    "answers": {
                        "A1": {"option": "Remove", "note": "My written decision wins."},
                        "B1": {"option": "Use current name", "note": ""},
                        "__proto__": {"option": "Change prototype", "note": "Saved prototype answer."},
                        "constructor": {"option": "Change constructor", "note": "Saved constructor answer."},
                    },
                }
                self.assertEqual(bridge.received_payloads, [submitted, submitted])
                self.assertEqual(script_errors, [])
            finally:
                stop_event.set()
                receiver.server_close()
                socket_path.unlink(missing_ok=True)
                bridge.shutdown()
                bridge.server_close()
                receiver_thread.join(2)
                bridge_thread.join(2)

    def test_storage_errors_stay_visible_without_blocking_answers_or_submission(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable")
        decisions = browser_decisions()
        page_html = decision_page.render_decision_page(decisions)
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            socket_path = Path(temporary) / "s"
            answers_path = Path(temporary) / "answers.json"
            receiver = decision_page.DecisionHTTPServer(
                socket_path, page_html, decisions, answers_path
            )
            stop_event = threading.Event()
            receiver_thread = threading.Thread(
                target=receiver.serve_until_complete,
                args=(stop_event,),
                daemon=True,
            )
            bridge = DecisionPageBridge(page_html, str(socket_path))
            bridge.fail_next_post = False
            bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
            receiver_thread.start()
            bridge_thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=executable, headless=True)
                    context = browser.new_context()
                    context.add_init_script("""
                        Object.defineProperty(Storage.prototype, "getItem", {
                            configurable: true,
                            value() { throw new Error("storage read blocked"); }
                        });
                        Object.defineProperty(Storage.prototype, "setItem", {
                            configurable: true,
                            value() { throw new Error("storage write blocked"); }
                        });
                    """)
                    page = context.new_page()
                    page.goto(f"http://127.0.0.1:{bridge.server_port}/")

                    warning = page.locator("#storage-warning")
                    self.assertTrue(warning.is_visible())
                    self.assertIn("草稿没能保存", warning.text_content())
                    item = page.locator('[data-item-id="A1"]')
                    item.locator('input[value="Remove"]').check()
                    item.locator("textarea").fill("Still available for submission.")
                    self.assertTrue(warning.is_visible())
                    self.assertFalse(item.locator("textarea").is_disabled())

                    page.locator("#submit").click()
                    page.locator("#status").get_by_text("已收到").wait_for()
                    self.assertTrue(warning.is_visible())
                    self.assertTrue(item.locator("textarea").is_disabled())
                    browser.close()

                receiver_thread.join(5)
                self.assertFalse(receiver_thread.is_alive())
                self.assertEqual(
                    json.loads(answers_path.read_text(encoding="utf-8"))["answers"]["A1"],
                    {"option": "Remove", "note": "Still available for submission."},
                )
            finally:
                stop_event.set()
                receiver.server_close()
                socket_path.unlink(missing_ok=True)
                bridge.shutdown()
                bridge.server_close()
                receiver_thread.join(2)
                bridge_thread.join(2)
