"""Exercise the decision page in Chromium through a local portal bridge."""

from contextlib import contextmanager
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


ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
DECISION_PAGE_DIRECTORY = ROOT / "repository-care" / "xy-cleanup" / "decision_page"
sys.path.insert(0, str(DECISION_PAGE_DIRECTORY))

import decision_page  # noqa: E402


def browser_decisions():
    return {
        "title": "Cleanup decisions",
        "intro": ["Review every item."],
        "groups": [
            {
                "title": "Repository",
                "notes": ["Choose the option that fits this item."],
                "items": [
                    {
                        "id": "A1",
                        "title": "Keep the feature",
                        "lines": ["What is needed: clear behavior."],
                        "options": ["Keep", "Remove"],
                        "recommended": "Keep",
                        "details": None,
                        "compare": [
                            {
                                "lines": "guide.md:1-1",
                                "before": "Keep this requirement.",
                                "after": "Keep this requirement.",
                            },
                            {
                                "lines": "guide.md:2-2",
                                "before": "This paragraph stays.",
                                "after": "This paragraph stays.",
                            },
                            {
                                "lines": "guide.md:3-3",
                                "before": "Old wording stays.",
                                "after": "New wording stays.",
                            },
                            {
                                "lines": "guide.md:4-4",
                                "before": "Remove this sentence.",
                                "after": "",
                            },
                            {
                                "lines": "guide.md:5-5",
                                "before": "Another paragraph stays.",
                                "after": "Another paragraph stays.",
                            },
                        ],
                    },
                    {
                        "id": "B1",
                        "title": "Name the feature",
                        "lines": ["Why: a short name helps."],
                        "options": ["Use current name", "Rename"],
                        "recommended": "Use current name",
                        "compare": None,
                        "details": None,
                    },
                    {
                        "id": "__proto__",
                        "title": "Prototype id",
                        "lines": ["This id is valid."],
                        "options": ["Keep prototype", "Change prototype"],
                        "recommended": "Keep prototype",
                        "compare": None,
                        "details": None,
                    },
                    {
                        "id": "constructor",
                        "title": "Constructor id",
                        "lines": ["This id is valid too."],
                        "options": ["Keep constructor", "Change constructor"],
                        "recommended": "Keep constructor",
                        "compare": None,
                        "details": None,
                    },
                ],
            }
        ],
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


@contextmanager
def browser_receiver(directory, page_html, decisions, answers_path, fail_next_post):
    socket_path = directory / "s"
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
    bridge.fail_next_post = fail_next_post
    bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    receiver_thread.start()
    bridge_thread.start()
    try:
        yield receiver_thread, bridge
    finally:
        stop_event.set()
        receiver.server_close()
        socket_path.unlink(missing_ok=True)
        bridge.shutdown()
        bridge.server_close()
        receiver_thread.join(2)
        bridge_thread.join(2)


class DecisionPageBrowserTest(unittest.TestCase):
    def test_compare_shows_counts_and_each_block_change_on_a_phone(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(
            executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable"
        )
        page_html = decision_page.render_decision_page(browser_decisions())
        page_errors = []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=executable, headless=True
            )
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.set_content(page_html)

            comparison = page.locator('[data-item-id="A1"] .comparison')
            count_text = comparison.locator(".compare-counts").inner_text()
            self.assertIn("原文", count_text)
            self.assertIn("改写后", count_text)
            self.assertIn("少了", count_text)
            self.assertEqual(
                comparison.locator(
                    ".compare-unchanged .compare-summary"
                ).all_inner_texts(),
                ["没改的 2 段", "没改的 1 段"],
            )
            self.assertEqual(
                comparison.locator(".compare-range").all_inner_texts(),
                [
                    "guide.md:1-1",
                    "guide.md:2-2",
                    "guide.md:3-3",
                    "guide.md:4-4",
                    "guide.md:5-5",
                ],
            )
            self.assertEqual(
                comparison.locator(".compare-deleted del").inner_text(),
                "Remove this sentence.",
            )
            self.assertEqual(
                comparison.locator(".compare-rewritten del").inner_text(),
                "Old wording stays.",
            )
            self.assertEqual(
                comparison.locator(".compare-rewritten ins").inner_text(),
                "New wording stays.",
            )
            self.assertEqual(page.locator('[data-item-id="B1"] .comparison').count(), 0)
            self.assertLessEqual(
                page.evaluate("document.documentElement.scrollWidth"),
                page.evaluate("window.innerWidth"),
            )
            browser.close()

        self.assertEqual(page_errors, [])

    def test_drafts_persist_after_reload_and_are_scoped_to_each_call(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(
            executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable"
        )
        decisions = browser_decisions()
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            answers_path = Path(temporary) / "answers.json"
            first_page = decision_page.render_decision_page(decisions)
            second_page = decision_page.render_decision_page(decisions)
            script_errors = []
            with browser_receiver(
                Path(temporary), first_page, decisions, answers_path, False
            ) as (_receiver_thread, bridge):
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
                    prototype_item = page.locator('[data-item-id="__proto__"]')
                    constructor_item = page.locator('[data-item-id="constructor"]')
                    self.assertTrue(
                        first_item.locator('input[value="Keep"]').is_checked()
                    )
                    self.assertEqual(first_item.locator(".suggested").count(), 1)
                    self.assertIn(
                        "写了字就以你写的为准",
                        first_item.locator("textarea").get_attribute("placeholder"),
                    )
                    first_item.locator('input[value="Remove"]').check()
                    first_item.locator("textarea").fill("Use the simpler behavior.")
                    page.locator('[data-item-id="B1"] textarea').fill("Keep this note.")
                    prototype_item.locator('input[value="Change prototype"]').check()
                    prototype_item.locator("textarea").fill("Prototype answer.")
                    constructor_item.locator(
                        'input[value="Change constructor"]'
                    ).check()
                    constructor_item.locator("textarea").fill("Constructor answer.")
                    self.assertIn(
                        "改了或写了 4 条", page.locator("#status").inner_text()
                    )

                    page.reload()
                    self.assertTrue(
                        first_item.locator('input[value="Remove"]').is_checked()
                    )
                    self.assertEqual(
                        first_item.locator("textarea").input_value(),
                        "Use the simpler behavior.",
                    )
                    self.assertTrue(
                        prototype_item.locator(
                            'input[value="Change prototype"]'
                        ).is_checked()
                    )
                    self.assertEqual(
                        prototype_item.locator("textarea").input_value(),
                        "Prototype answer.",
                    )
                    self.assertTrue(
                        constructor_item.locator(
                            'input[value="Change constructor"]'
                        ).is_checked()
                    )
                    self.assertEqual(
                        constructor_item.locator("textarea").input_value(),
                        "Constructor answer.",
                    )

                    bridge.page = second_page
                    page.reload()
                    first_item = page.locator('[data-item-id="A1"]')
                    self.assertTrue(
                        first_item.locator('input[value="Keep"]').is_checked()
                    )
                    self.assertEqual(first_item.locator("textarea").input_value(), "")
                    self.assertTrue(
                        page.locator(
                            '[data-item-id="B1"] input[value="Use current name"]'
                        ).is_checked()
                    )
                    self.assertTrue(
                        page.locator(
                            '[data-item-id="__proto__"] input[value="Keep prototype"]'
                        ).is_checked()
                    )
                    self.assertTrue(
                        page.locator(
                            '[data-item-id="constructor"] input[value="Keep constructor"]'
                        ).is_checked()
                    )
                    self.assertFalse(
                        first_item.evaluate(
                            "element => element.classList.contains('changed')"
                        )
                    )
                    browser.close()

            self.assertFalse(answers_path.exists())
            self.assertEqual(script_errors, [])

    def test_phone_submission_keeps_answers_after_failure_and_saves_on_retry(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(
            executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable"
        )
        decisions = browser_decisions()
        details_lines = [
            "alpha：第一条。",
            "draft-" * 80 + "<img src=x onerror=window.pwned=1>",
        ]
        decisions["groups"][0]["items"][0]["details"] = {
            "title": "起草条目 2 条",
            "lines": details_lines,
        }
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            answers_path = Path(temporary) / "answers.json"
            page_html = decision_page.render_decision_page(decisions)
            script_errors = []
            with browser_receiver(
                Path(temporary), page_html, decisions, answers_path, True
            ) as (receiver_thread, bridge):
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=executable,
                        headless=True,
                    )
                    page = browser.new_page(viewport={"width": 390, "height": 844})
                    page.on("pageerror", lambda error: script_errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{bridge.server_port}/")
                    first_item = page.locator('[data-item-id="A1"]')
                    disclosure = first_item.locator(".decision-details")
                    self.assertFalse(disclosure.evaluate("element => element.open"))
                    self.assertEqual(
                        disclosure.locator("summary").inner_text(), "起草条目 2 条"
                    )
                    self.assertFalse(
                        disclosure.locator(".details-line").first.is_visible()
                    )
                    disclosure.locator("summary").click()
                    self.assertEqual(
                        disclosure.locator(".details-line").all_inner_texts(),
                        details_lines,
                    )
                    self.assertIsNone(page.evaluate("window.pwned"))
                    prototype_item = page.locator('[data-item-id="__proto__"]')
                    constructor_item = page.locator('[data-item-id="constructor"]')
                    first_item.locator('input[value="Remove"]').check()
                    first_item.locator("textarea").fill("My written decision wins.")
                    prototype_item.locator('input[value="Change prototype"]').check()
                    prototype_item.locator("textarea").fill("Saved prototype answer.")
                    constructor_item.locator(
                        'input[value="Change constructor"]'
                    ).check()
                    constructor_item.locator("textarea").fill(
                        "Saved constructor answer."
                    )

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
                    self.assertIn(
                        "temporary test failure",
                        page.locator("#feedback").inner_text(),
                    )
                    self.assertTrue(
                        first_item.locator('input[value="Remove"]').is_checked()
                    )
                    self.assertEqual(
                        first_item.locator("textarea").input_value(),
                        "My written decision wins.",
                    )
                    self.assertFalse(first_item.locator("textarea").is_disabled())

                    page.locator("#submit").click()
                    page.locator("#status").get_by_text("已收到").wait_for()
                    self.assertTrue(first_item.locator("textarea").is_disabled())
                    browser.close()

                receiver_thread.join(5)
                self.assertFalse(receiver_thread.is_alive())
                expected_answers = {
                    "A1": {"option": "Remove", "note": "My written decision wins."},
                    "B1": {"option": "Use current name", "note": ""},
                    "__proto__": {
                        "option": "Change prototype",
                        "note": "Saved prototype answer.",
                    },
                    "constructor": {
                        "option": "Change constructor",
                        "note": "Saved constructor answer.",
                    },
                }
                saved_answers = json.loads(answers_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    saved_answers,
                    {"title": "Cleanup decisions", "answers": expected_answers},
                )
                self.assertNotIn("details", saved_answers["answers"]["A1"])
                submitted = {"answers": expected_answers}
                self.assertEqual(bridge.received_payloads, [submitted, submitted])
                self.assertEqual(script_errors, [])

    def test_storage_errors_stay_visible_without_blocking_answers_or_submission(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(
            executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable"
        )
        decisions = browser_decisions()
        page_html = decision_page.render_decision_page(decisions)
        with tempfile.TemporaryDirectory(prefix="d", dir="/tmp") as temporary:
            answers_path = Path(temporary) / "answers.json"
            with browser_receiver(
                Path(temporary), page_html, decisions, answers_path, False
            ) as (receiver_thread, bridge):
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=executable, headless=True
                    )
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
                    json.loads(answers_path.read_text(encoding="utf-8"))["answers"][
                        "A1"
                    ],
                    {"option": "Remove", "note": "Still available for submission."},
                )
