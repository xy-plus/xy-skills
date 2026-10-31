"""Browser acceptance through real LikeC4 rendering and a loopback HTTP server."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from urllib.parse import quote, urlsplit

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.constants import STANDARD_KINDS
from repo_map_lib.likec4 import export_model
from repo_map_lib.structure import structure_digest
from map_data_contract_assertions import assert_map_data_contract


COUNT_LABELS = (
    ("code", "代码"),
    ("docs", "文档"),
    ("comments", "注释"),
    ("tests", "测试"),
)
REPO_MAP_SCRIPT = Path(__file__).resolve().parents[1] / "repo_map.py"


class QuietHTTPHandler(SimpleHTTPRequestHandler):
    def log_message(self, format_string, *arguments):
        return


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def add_repository_files(root, files):
    for relative, content in files.items():
        write_text(Path(root) / relative, content)


def initialize_repository(root, tracked_paths):
    subprocess.run(["git", "init", "--initial-branch=main", str(root)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(root), "add", "--", *tracked_paths],
                   check=True, capture_output=True, text=True)


def write_map(root, root_id, model):
    map_root = Path(root) / "repo-map"
    map_root.mkdir()
    write_text(map_root / "likec4.config.json",
               json.dumps({"name": root_id}, ensure_ascii=False) + "\n")
    write_text(map_root / "kinds.c4", STANDARD_KINDS)
    write_text(map_root / "approvals.txt", "")
    write_text(map_root / "model.c4", model)
    return map_root


def run_repo_map(*arguments):
    result = subprocess.run(
        [sys.executable, "-B", str(REPO_MAP_SCRIPT), *map(str, arguments)],
        check=False, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"repo_map.py {' '.join(map(str, arguments))} exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout


def build_approved_parent_child_fixture(root, site_root):
    parent = root / "workspace"
    engine = parent / "vendor" / "engine"
    solo = parent / "vendor" / "solo"
    for repository in (parent, engine, solo):
        repository.mkdir(parents=True, exist_ok=True)

    add_repository_files(parent, {
        ".gitignore": "/vendor/engine/\n/vendor/solo/\n",
        "README.md": "Workspace repository documentation.\n",
        "docs/session.md": "Session workflow documentation.\n",
        "docs/process.md": "Process documentation.\n",
        "docs/step.md": "Single step documentation.\n",
        "docs/events.md": "Event documentation.\n",
        "src/session.py": '"""Public session module."""\n\ndef open_session():\n'
                         '    """Open one session."""\n    # record the start\n    return True\n',
        "src/process.py": '"""Public process module."""\n\ndef run_step():\n'
                          '    """Run one step."""\n    return True\n',
        "src/step.py": '"""Public step module."""\n\ndef record_step():\n'
                       '    """Record one step."""\n    return True\n',
        "src/events.py": '"""Public event module."""\n\ndef publish_event():\n'
                         '    """Publish one event."""\n    return True\n',
        "tests/test_process.py": "def test_run_step():\n    assert True\n",
        "tests/test_step.py": "def test_record_step():\n    assert True\n",
        "tests/test_events.py": "def test_publish_event():\n    assert True\n",
    })
    add_repository_files(engine, {
        "README.md": "Engine repository documentation.\n",
        "docs/cache.md": "Cache documentation.\n",
        "docs/ledger.md": "Ledger documentation.\n",
        "src/engine.py": '"""Public engine module."""\n\ndef start_engine():\n'
                         '    """Start the engine."""\n    return True\n',
        "src/cache.py": '"""Public cache module."""\n\ndef refresh_cache():\n'
                        '    """Refresh cached data."""\n    return True\n',
        "src/ledger.py": '"""Public ledger module."""\n\ndef write_ledger():\n'
                         '    """Write one ledger entry."""\n    return True\n',
        "tests/test_cache.py": "def test_refresh_cache():\n    assert True\n",
        "tests/test_ledger.py": "def test_write_ledger():\n    assert True\n",
    })
    add_repository_files(solo, {
        "README.md": "Single-node repository documentation.\n",
        "src/solo.py": '"""Public single-node module."""\n\ndef run_solo():\n'
                       '    """Run the only node."""\n    return True\n',
        "tests/test_solo.py": "def test_run_solo():\n    assert True\n",
    })

    initialize_repository(parent, [".gitignore", "README.md", "docs", "src", "tests"])
    initialize_repository(engine, ["README.md", "docs", "src", "tests"])
    initialize_repository(solo, ["README.md", "src", "tests"])

    write_map(parent, "workspace", """model {
  workspace = node '工作区' {
    description '包含会话流程和两个子仓。'
    link ../README.md 'docs'
    link ../.gitignore 'data'
    session = node '会话工作流' {
      description '运行会话步骤并发布事件。'
      link ../src/session.py 'code'
      link ../docs/session.md 'docs'
      process = node '执行步骤' {
        description '执行当前会话步骤。'
        link ../src/process.py 'code'
        link ../docs/process.md 'docs'
        link ../tests/test_process.py 'tests'
        step = node '单步记录' {
          description '记录当前步骤的结果。'
          link ../src/step.py 'code'
          link ../docs/step.md 'docs'
          link ../tests/test_step.py 'tests'
        }
      }
      events = node '事件处理' {
        description '处理会话事件。'
        link ../src/events.py 'code'
        link ../docs/events.md 'docs'
        link ../tests/test_events.py 'tests'
      }
    }
    engine = repo '引擎子仓' {
      description '维护缓存和账本。'
      link ../vendor/engine/ 'repo'
    }
    solo = repo '单节点子仓' {
      description '只包含一个根节点。'
      link ../vendor/solo/ 'repo'
    }
  }
  workspace.session.process -> workspace.session.events '随后触发'
  workspace.session.process -> workspace.session.events '重试后触发'
  workspace.session.process.step -> workspace.session.events '跨层调用'
}
""")
    write_map(engine, "engine", """model {
  engine = node '引擎' {
    description '运行缓存和账本功能。'
    link ../README.md 'docs'
    link ../src/engine.py 'code'
    cache = node '缓存' {
      description '保存并刷新市场数据。'
      link ../src/cache.py 'code'
      link ../docs/cache.md 'docs'
      link ../tests/test_cache.py 'tests'
    }
    ledger = node '账本' {
      description '记录市场数据变化。'
      link ../src/ledger.py 'code'
      link ../docs/ledger.md 'docs'
      link ../tests/test_ledger.py 'tests'
    }
  }
  engine.cache -> engine.ledger '刷新后记录'
}
""")
    write_map(solo, "solo", """model {
  solo = node '单节点根' {
    description '运行唯一的仓库功能。'
    link ../README.md 'docs'
    link ../src/solo.py 'code'
    link ../tests/test_solo.py 'tests'
  }
}
""")

    draft_output = site_root / "draft"
    run_repo_map("render", "--repo", parent, "--mode", "草稿", "--out", draft_output)

    repository_maps = [parent / "repo-map", engine / "repo-map", solo / "repo-map"]
    exported_models = [export_model(repository_map) for repository_map in repository_maps]
    questions = [
        {"question": f"请确认 repo-map sha256 {structure_digest(model)}",
         "options": [{"label": "同意改图"}, {"label": "不同意"}]}
        for model in exported_models
    ]
    answers = {question["question"]: "同意改图" for question in questions}
    quote_path = root / "xy-goal" / "quotes" / "57.md"
    write_text(quote_path, json.dumps({
        "tool_input": {"questions": questions, "answers": answers},
        "tool_response": {"questions": questions, "answers": dict(answers)},
    }, ensure_ascii=False))
    for repository in (parent, engine, solo):
        run_repo_map("record", "--repo", repository, "--approval", quote_path)
        approval_rows = (repository / "repo-map" / "approvals.txt").read_text(
            encoding="utf-8").strip()
        if not approval_rows:
            raise AssertionError(f"record 未写入同意记录：{repository}")
    run_repo_map("check", "--repo", parent)
    formal_output = site_root / "formal"
    run_repo_map("render", "--repo", parent, "--mode", "正式", "--out", formal_output)
    return parent, engine, solo, draft_output, formal_output


class ParentChildBrowserTestSupport:
    @classmethod
    def setUpClass(cls):
        raw_browser = os.environ["XY_REPO_MAP_CHROMIUM"]
        cls.browser_path = Path(raw_browser)
        if not cls.browser_path.is_absolute() or not cls.browser_path.is_file():
            raise FileNotFoundError(f"XY_REPO_MAP_CHROMIUM 不是 Chromium 可执行文件：{raw_browser}")
        if not os.access(cls.browser_path, os.X_OK):
            raise FileNotFoundError(f"XY_REPO_MAP_CHROMIUM 不可执行：{raw_browser}")

        cls.temporary = tempfile.TemporaryDirectory(prefix="xy-repo-map-browser-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.site_root = cls.root / "sites"
        cls.site_root.mkdir()
        cls.parent, cls.engine, cls.solo, cls.draft_output, cls.formal_output = (
            build_approved_parent_child_fixture(cls.root, cls.site_root))
        draft_html = (cls.draft_output / "index.html").read_text(encoding="utf-8")
        formal_html = (cls.formal_output / "index.html").read_text(encoding="utf-8")
        if "草稿·未经同意" not in draft_html or not (cls.draft_output / "DRAFT").is_file():
            raise AssertionError("草稿和正式产物的 DRAFT 标记不符")
        if "DRAFT" in formal_html:
            raise AssertionError("正式 index.html 含 DRAFT")

        handler = partial(QuietHTTPHandler, directory=str(cls.site_root))
        cls.http_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.http_thread = Thread(target=cls.http_server.serve_forever, daemon=True)
        cls.http_thread.start()
        cls.addClassCleanup(cls.stop_http_server)
        if cls.http_server.server_address[0] != "127.0.0.1":
            raise AssertionError("浏览器验收服务必须只监听 127.0.0.1")
        cls.base_url = f"http://127.0.0.1:{cls.http_server.server_port}"

    @classmethod
    def stop_http_server(cls):
        if hasattr(cls, "http_server"):
            cls.http_server.shutdown()
            cls.http_server.server_close()
            cls.http_thread.join(timeout=5)

    def open_browser(self, playwright):
        return playwright.chromium.launch(headless=True, executable_path=str(self.browser_path))

    def open_page(self, browser, mode, route, viewport, touch):
        page = browser.new_page(viewport=viewport, device_scale_factor=1,
                                is_mobile=touch, has_touch=touch)
        remote_requests = []
        page.on("request", lambda request: remote_requests.append(request.url)
                if urlsplit(request.url).netloc != urlsplit(self.base_url).netloc else None)
        address = f"{self.base_url}/{mode}/index.html"
        if route is not None:
            address += "#" + quote(route, safe="/.")
        page.goto(address, wait_until="load")
        return page, remote_requests

    def page_data(self, page):
        return page.evaluate(
            "JSON.parse(document.getElementById('page-data').textContent)")

    def map_data(self, mode):
        output = self.draft_output if mode == "draft" else self.formal_output
        document = json.loads((output / "map-data.json").read_text(encoding="utf-8"))
        assert_map_data_contract(self, document)
        return document

    def wait_for_view(self, page, mode, current):
        page.wait_for_function(
            "([mode, current]) => { const map = document.getElementById('map'); "
            "return map.dataset.mode === mode && map.dataset.current === current; }",
            arg=[mode, current or ""],
        )
        page.wait_for_selector("#scene")

    def assert_address(self, page, route):
        self.assertEqual(page.evaluate("decodeURIComponent(location.hash.slice(1))"), route)

    def assert_visible_totals(self, page, map_document, expected_ids):
        card_ids = page.locator(".card").evaluate_all(
            "cards => cards.map(card => card.getAttribute('data-node-id'))")
        self.assertEqual(set(card_ids), set(expected_ids))
        self.assertEqual(len(card_ids), len(expected_ids))
        for element_id in expected_ids:
            expected = map_document["nodes"][element_id]["total"]
            actual = page.locator(
                f'.card[data-node-id="{element_id}"] .metric-text').all_text_contents()
            self.assertEqual(actual, [f"{label} {expected[name]}"
                                      for name, label in COUNT_LABELS], element_id)

    def expected_drill_ids(self, map_document, page_document, current):
        child_root = page_document["repo_roots"].get(current, current)
        children = {element_id for element_id, node in map_document["nodes"].items()
                    if node["parent"] == child_root}
        return children or {current}

    def assert_relation_visible(self, page, source, target, title):
        data = self.page_data(page)
        route = page.evaluate("decodeURIComponent(location.hash.slice(1))")
        route_mode, current = route.split("/", 1)
        self.assertEqual(route_mode, "钻入")
        visible_relations = data["drill_views"][current]["relations"]
        self.assertIn({"source": source, "target": target, "title": title}, visible_relations)
        edge = page.locator(".edge")
        self.assertGreater(edge.count(), 0)
        edge_style = edge.first.evaluate("""element => {
          const style = getComputedStyle(element);
          return {display:style.display, visibility:style.visibility,
            stroke:style.stroke, strokeWidth:style.strokeWidth,
            path:element.getAttribute("d")};
        }""")
        self.assertNotEqual(edge_style["display"], "none")
        self.assertEqual(edge_style["visibility"], "visible")
        self.assertNotEqual(edge_style["stroke"], "none")
        self.assertNotEqual(edge_style["strokeWidth"], "0px")
        self.assertTrue(edge_style["path"])
        self.assertIn(title, page.locator(".edge-label").all_text_contents())
        self.assertTrue(page.locator(".edge-label-bg").is_visible())


class RepositoryMapBrowserAcceptanceTest(ParentChildBrowserTestSupport, unittest.TestCase):
    def test_real_render_page_covers_expansion_drill_history_and_relations(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page, remote_requests = self.open_page(
                    browser, "draft", None, {"width": 1440, "height": 900}, False)
                map_document = self.map_data("draft")
                page_document = self.page_data(page)
                self.wait_for_view(page, "expand", None)
                self.assert_address(page, "展开")
                self.assertEqual(set(page_document["nodes"]), set(map_document["nodes"]))
                expanded_ids = set(map_document["nodes"]) - set(
                    page_document["repo_roots"].values())
                self.assert_visible_totals(page, map_document, expanded_ids)
                for placeholder, child_root in page_document["repo_roots"].items():
                    self.assertEqual(map_document["nodes"][placeholder]["total"],
                                     map_document["nodes"][child_root]["total"])
                self.assertEqual(page.locator("#draft").inner_text(), "草稿·未经同意")

                page.locator("#mode").click()
                self.wait_for_view(page, "drill", "workspace")
                self.assert_address(page, "钻入/workspace")
                root_ids = self.expected_drill_ids(map_document, page_document, "workspace")
                self.assert_visible_totals(page, map_document, root_ids)
                page.locator('.card[data-node-id="workspace.session"]').click()
                self.wait_for_view(page, "drill", "workspace.session")
                self.assert_address(page, "钻入/workspace.session")
                session_ids = self.expected_drill_ids(
                    map_document, page_document, "workspace.session")
                self.assert_visible_totals(page, map_document, session_ids)
                self.assert_relation_visible(
                    page, "workspace.session.process", "workspace.session.events",
                    "随后触发 · 重试后触发 · 跨层调用")

                page.locator('.card[data-node-id="workspace.session.process"]').click()
                self.wait_for_view(page, "drill", "workspace.session.process")
                self.assert_address(page, "钻入/workspace.session.process")
                self.assert_visible_totals(
                    page, map_document,
                    self.expected_drill_ids(map_document, page_document,
                                            "workspace.session.process"))
                self.assertEqual(page.locator("#crumbs button").all_text_contents(),
                                 ["工作区", "会话工作流", "执行步骤"])
                process_ids = self.expected_drill_ids(
                    map_document, page_document, "workspace.session.process")
                self.assertEqual(process_ids, {"workspace.session.process.step"})

                page.go_back()
                self.wait_for_view(page, "drill", "workspace.session")
                self.assert_address(page, "钻入/workspace.session")
                self.assert_visible_totals(page, map_document, session_ids)
                page.go_back()
                self.wait_for_view(page, "drill", "workspace")
                self.assert_address(page, "钻入/workspace")
                self.assert_visible_totals(page, map_document, root_ids)
                page.go_forward()
                self.wait_for_view(page, "drill", "workspace.session")
                self.assert_address(page, "钻入/workspace.session")
                self.assert_visible_totals(page, map_document, session_ids)
                page.go_forward()
                self.wait_for_view(page, "drill", "workspace.session.process")
                self.assert_address(page, "钻入/workspace.session.process")
                page.locator("#crumbs button").nth(1).click()
                self.wait_for_view(page, "drill", "workspace.session")
                self.assert_address(page, "钻入/workspace.session")
                page.locator("#crumbs button").nth(0).click()
                self.wait_for_view(page, "drill", "workspace")
                self.assert_address(page, "钻入/workspace")
                self.assert_visible_totals(page, map_document, root_ids)
                self.assertEqual(remote_requests, [])
            finally:
                browser.close()

    def test_real_render_subrepository_placeholders_and_deep_links(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                deep_route = "钻入/workspace.session.process.step"
                page, remote_requests = self.open_page(
                    browser, "draft", deep_route, {"width": 1440, "height": 900}, False)
                map_document = self.map_data("draft")
                page_document = self.page_data(page)
                self.wait_for_view(page, "drill", "workspace.session.process.step")
                self.assert_address(page, deep_route)
                self.assertEqual(page.locator("#crumbs button").all_text_contents(),
                                 ["工作区", "会话工作流", "执行步骤", "单步记录"])
                expected_leaf = self.expected_drill_ids(
                    map_document, page_document, "workspace.session.process.step")
                self.assert_visible_totals(page, map_document, expected_leaf)
                page.reload(wait_until="load")
                self.wait_for_view(page, "drill", "workspace.session.process.step")
                self.assert_address(page, deep_route)
                self.assert_visible_totals(page, map_document, expected_leaf)

                page.goto(f"{self.base_url}/draft/index.html#%E9%92%BB%E5%85%A5/workspace",
                          wait_until="load")
                self.wait_for_view(page, "drill", "workspace")
                map_document = self.map_data("draft")
                page_document = self.page_data(page)
                root_ids = self.expected_drill_ids(map_document, page_document, "workspace")
                page.locator('.card[data-node-id="workspace.engine"]').click()
                self.wait_for_view(page, "drill", "workspace.engine")
                self.assert_address(page, "钻入/workspace.engine")
                engine_ids = self.expected_drill_ids(
                    map_document, page_document, "workspace.engine")
                self.assert_visible_totals(page, map_document, engine_ids)
                self.assertEqual(set(engine_ids), {"engine.cache", "engine.ledger"})
                self.assert_relation_visible(page, "engine.cache", "engine.ledger", "刷新后记录")
                self.assertEqual(page.locator("#crumbs button").all_text_contents(),
                                 ["工作区", "引擎子仓"])

                page.locator("#crumbs button").nth(0).click()
                self.wait_for_view(page, "drill", "workspace")
                solo = page.locator('.card[data-node-id="workspace.solo"]')
                self.assertNotIn("clickable", solo.get_attribute("class"))
                self.assertEqual(solo.locator(".card-shape").get_attribute(
                    "stroke-dasharray"), "none")
                self.assertNotEqual(
                    solo.evaluate("element => getComputedStyle(element).cursor"), "pointer")
                address_before_click = page.evaluate(
                    "decodeURIComponent(location.hash.slice(1))")
                solo.click()
                self.assertEqual(page.evaluate("decodeURIComponent(location.hash.slice(1))"),
                                 address_before_click)
                self.assert_visible_totals(page, map_document, root_ids)
                self.assertEqual(remote_requests, [])
            finally:
                browser.close()

    def test_real_formal_render_has_no_draft_banner_or_marker(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page, remote_requests = self.open_page(
                    browser, "formal", None, {"width": 1440, "height": 900}, False)
                map_document = self.map_data("正式")
                page_document = self.page_data(page)
                self.wait_for_view(page, "expand", None)
                self.assertEqual(set(page_document["nodes"]), set(map_document["nodes"]))
                expanded_ids = set(map_document["nodes"]) - set(
                    page_document["repo_roots"].values())
                self.assert_visible_totals(page, map_document, expanded_ids)
                self.assertEqual(page.locator("#draft").inner_text(), "")
                self.assertFalse((self.formal_output / "DRAFT").exists())
                self.assertEqual(remote_requests, [])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
