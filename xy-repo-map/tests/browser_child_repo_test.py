"""Automated browser acceptance for the rendered repository map page."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import quote

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.site import write_site


COUNT_LABELS = (
    ("code", "代码"),
    ("docs", "文档"),
    ("comments", "注释"),
    ("tests", "测试"),
)
ELEMENTS = {
    "workspace": ("工作区", "node", None, (1, 1, 1, 1)),
    "workspace.session": ("会话", "node", "workspace", (2, 1, 2, 1)),
    "workspace.session.process": (
        "运行流程", "node", "workspace.session", (1, 2, 1, 2)),
    "workspace.session.process.leaf": (
        "单步执行", "node", "workspace.session.process", (3, 2, 1, 1)),
    "workspace.engine": ("引擎仓", "repo", "workspace", (0, 0, 0, 0)),
    "engine": ("子仓根", "node", "workspace.engine", (1, 1, 1, 1)),
    "engine.cache": ("缓存", "node", "engine", (2, 1, 2, 1)),
    "workspace.solo": ("单节点仓", "repo", "workspace", (0, 0, 0, 0)),
    "solo": ("单节点根", "node", "workspace.solo", (1, 2, 1, 3)),
}
REPOSITORY_ROOTS = {"workspace.engine": "engine", "workspace.solo": "solo"}
# Child repository roots are represented by their parent placeholder card.
VISIBLE_EXPANDED_NODES = {
    "workspace", "workspace.session", "workspace.session.process",
    "workspace.session.process.leaf", "workspace.engine", "engine.cache",
    "workspace.solo",
}


def make_documents(mode):
    own_counts = {
        element_id: dict(zip((name for name, _ in COUNT_LABELS), values))
        for element_id, (_, _, _, values) in ELEMENTS.items()
    }
    child_ids = {element_id: [] for element_id in ELEMENTS}
    for element_id, (_, _, parent, _) in ELEMENTS.items():
        if parent is not None:
            child_ids[parent].append(element_id)

    totals = {}

    def total_for(element_id):
        if element_id in totals:
            return totals[element_id]
        total = dict(own_counts[element_id])
        for child_id in child_ids[element_id]:
            child_total = total_for(child_id)
            for name, _ in COUNT_LABELS:
                total[name] += child_total[name]
        totals[element_id] = total
        return total

    for element_id, (_, _, parent, _) in ELEMENTS.items():
        if parent is None:
            total_for(element_id)

    map_nodes = {
        element_id: {
            "title": title,
            "kind": kind,
            "parent": parent,
            "own": own_counts[element_id],
            "total": totals[element_id],
        }
        for element_id, (title, kind, parent, _) in ELEMENTS.items()
    }
    page_nodes = {
        element_id: {
            **node,
            "description": f"{node['title']} 的功能描述。",
            "coverage": "Python 1/1",
        }
        for element_id, node in map_nodes.items()
    }
    drill_views = {
        "workspace": {
            "nodes": sorted(child_ids["workspace"]), "relations": [],
            "breadcrumbs": ["workspace"],
        },
        "workspace.session": {
            "nodes": ["workspace.session.process"], "relations": [],
            "breadcrumbs": ["workspace", "workspace.session"],
        },
        "workspace.session.process": {
            "nodes": ["workspace.session.process.leaf"], "relations": [],
            "breadcrumbs": ["workspace", "workspace.session", "workspace.session.process"],
        },
        "workspace.session.process.leaf": {
            "nodes": ["workspace.session.process.leaf"], "relations": [],
            "breadcrumbs": ["workspace", "workspace.session", "workspace.session.process",
                            "workspace.session.process.leaf"],
        },
        "workspace.engine": {
            "nodes": ["engine.cache"], "relations": [],
            "breadcrumbs": ["workspace", "workspace.engine"],
        },
        "engine": {
            "nodes": ["engine.cache"], "relations": [],
            "breadcrumbs": ["workspace", "workspace.engine"],
        },
        "engine.cache": {
            "nodes": ["engine.cache"], "relations": [],
            "breadcrumbs": ["workspace", "workspace.engine", "engine.cache"],
        },
        "workspace.solo": {
            "nodes": ["workspace.solo"], "relations": [],
            "breadcrumbs": ["workspace", "workspace.solo"],
        },
        "solo": {
            "nodes": ["workspace.solo"], "relations": [],
            "breadcrumbs": ["workspace", "workspace.solo"],
        },
    }
    map_data = {"mode": mode, "nodes": map_nodes}
    page_data = {
        "mode": mode,
        "top_roots": ["workspace"],
        "nodes": page_nodes,
        "relations": [],
        "repo_roots": REPOSITORY_ROOTS,
        "drill_views": drill_views,
    }
    return map_data, page_data


class RepositoryMapBrowserAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output_root = Path(self.temporary.name)
        self.documents = {}
        for mode, directory_name in (("草稿", "draft"), ("正式", "formal")):
            map_data, page_data = make_documents(mode)
            output = self.output_root / directory_name
            output.mkdir()
            write_site(map_data, page_data, output)
            self.documents[mode] = {
                "directory": output,
                "map": json.loads((output / "map-data.json").read_text(encoding="utf-8")),
            }

    def open_browser(self, playwright):
        return playwright.chromium.launch(
            headless=True, executable_path=os.environ["XY_REPO_MAP_CHROMIUM"])

    def open_page(self, browser, mode, route=None):
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        external_requests = []
        page.on("request", lambda request: external_requests.append(request.url)
                if request.url.startswith(("http://", "https://")) else None)
        page_path = self.documents[mode]["directory"] / "index.html"
        address = page_path.as_uri()
        if route is not None:
            address += "#" + quote(route, safe="/.")
        page.goto(address, wait_until="load")
        return page, external_requests

    def wait_for_view(self, page, mode, current):
        page.wait_for_function(
            "([mode, current]) => { const map = document.getElementById('map'); "
            "return map.dataset.mode === mode && map.dataset.current === current; }",
            arg=["expand" if mode == "展开" else "drill", current or ""],
        )
        page.wait_for_selector("#scene")

    def assert_address(self, page, route):
        self.assertEqual(page.evaluate("decodeURIComponent(location.hash.slice(1))"), route)

    def assert_page_data_has_map_nodes(self, page, map_data):
        page_node_ids = page.evaluate(
            "Object.keys(JSON.parse(document.getElementById('page-data').textContent).nodes)")
        self.assertEqual(set(page_node_ids), set(map_data["nodes"]))

    def assert_visible_totals(self, page, map_data, expected_node_ids):
        cards = page.locator(".card").evaluate_all(
            "items => items.map(item => item.getAttribute('data-node-id'))")
        self.assertEqual(set(cards), set(expected_node_ids))
        self.assertEqual(len(cards), len(expected_node_ids))
        for element_id in expected_node_ids:
            expected = map_data["nodes"][element_id]["total"]
            self.assertTrue(all(expected[name] > 0 for name, _ in COUNT_LABELS), element_id)
            actual = page.locator(
                f'.card[data-node-id="{element_id}"] .metric-text').all_text_contents()
            self.assertEqual(actual, [f"{label} {expected[name]}"
                                      for name, label in COUNT_LABELS], element_id)

    def test_expanded_view_displays_each_visible_node_and_its_map_totals(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page, external_requests = self.open_page(browser, "草稿")
                draft_map = self.documents["草稿"]["map"]
                self.wait_for_view(page, "展开", None)
                self.assert_address(page, "展开")
                self.assert_page_data_has_map_nodes(page, draft_map)
                self.assertEqual(set(page.locator(".card").evaluate_all(
                    "items => items.map(item => item.getAttribute('data-node-id'))")),
                    VISIBLE_EXPANDED_NODES)
                self.assert_visible_totals(page, draft_map, VISIBLE_EXPANDED_NODES)
                for placeholder, child_root in REPOSITORY_ROOTS.items():
                    self.assertEqual(draft_map["nodes"][placeholder]["total"],
                                     draft_map["nodes"][child_root]["total"])
                self.assertEqual(page.locator("#draft").inner_text(), "草稿·未经同意")
                self.assertEqual(external_requests, [])
            finally:
                browser.close()

    def test_click_drill_breadcrumb_history_and_subrepository_navigation(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page, external_requests = self.open_page(browser, "草稿")
                map_data = self.documents["草稿"]["map"]
                self.wait_for_view(page, "展开", None)
                page.locator("#mode").click()
                self.wait_for_view(page, "钻入", "workspace")
                self.assert_address(page, "钻入/workspace")
                root_nodes = {"workspace.session", "workspace.engine", "workspace.solo"}
                self.assert_visible_totals(page, map_data, root_nodes)

                page.locator('.card[data-node-id="workspace.session"]').click()
                self.wait_for_view(page, "钻入", "workspace.session")
                self.assert_address(page, "钻入/workspace.session")
                self.assert_visible_totals(page, map_data, {"workspace.session.process"})
                page.locator('.card[data-node-id="workspace.session.process"]').click()
                self.wait_for_view(page, "钻入", "workspace.session.process")
                self.assert_address(page, "钻入/workspace.session.process")
                self.assert_visible_totals(
                    page, map_data, {"workspace.session.process.leaf"})
                self.assertEqual(page.locator("#crumbs button").all_text_contents(),
                                 ["工作区", "会话", "运行流程"])

                page.go_back()
                self.wait_for_view(page, "钻入", "workspace.session")
                self.assert_address(page, "钻入/workspace.session")
                self.assert_visible_totals(page, map_data, {"workspace.session.process"})
                page.go_back()
                self.wait_for_view(page, "钻入", "workspace")
                self.assert_address(page, "钻入/workspace")
                self.assert_visible_totals(page, map_data, root_nodes)
                page.go_forward()
                self.wait_for_view(page, "钻入", "workspace.session")
                self.assert_address(page, "钻入/workspace.session")
                self.assert_visible_totals(page, map_data, {"workspace.session.process"})
                page.go_forward()
                self.wait_for_view(page, "钻入", "workspace.session.process")
                self.assert_address(page, "钻入/workspace.session.process")
                self.assert_visible_totals(
                    page, map_data, {"workspace.session.process.leaf"})

                page.locator("#crumbs button").nth(1).click()
                self.wait_for_view(page, "钻入", "workspace.session")
                self.assert_visible_totals(page, map_data, {"workspace.session.process"})
                page.locator("#crumbs button").nth(0).click()
                self.wait_for_view(page, "钻入", "workspace")
                self.assert_visible_totals(page, map_data, root_nodes)

                page.locator('.card[data-node-id="workspace.engine"]').click()
                self.wait_for_view(page, "钻入", "workspace.engine")
                self.assert_address(page, "钻入/workspace.engine")
                self.assert_visible_totals(page, map_data, {"engine.cache"})
                self.assertEqual(page.locator("#crumbs button").all_text_contents(),
                                 ["工作区", "引擎仓"])

                page.locator("#crumbs button").nth(0).click()
                self.wait_for_view(page, "钻入", "workspace")
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
                self.assert_visible_totals(page, map_data, root_nodes)
                self.assertEqual(external_requests, [])
            finally:
                browser.close()

    def test_deep_link_reload_and_formal_artifact_preserve_mode(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                deep_route = "钻入/workspace.session.process"
                page, external_requests = self.open_page(browser, "草稿", deep_route)
                map_data = self.documents["草稿"]["map"]
                self.wait_for_view(page, "钻入", "workspace.session.process")
                self.assert_address(page, deep_route)
                self.assertEqual(page.locator("#crumbs button").all_text_contents(),
                                 ["工作区", "会话", "运行流程"])
                self.assert_visible_totals(
                    page, map_data, {"workspace.session.process.leaf"})
                self.assertEqual(page.locator("#draft").inner_text(), "草稿·未经同意")
                page.reload(wait_until="load")
                self.wait_for_view(page, "钻入", "workspace.session.process")
                self.assert_address(page, deep_route)
                self.assert_visible_totals(
                    page, map_data, {"workspace.session.process.leaf"})

                formal_page, formal_requests = self.open_page(browser, "正式")
                self.wait_for_view(formal_page, "展开", None)
                formal_map = self.documents["正式"]["map"]
                self.assert_page_data_has_map_nodes(formal_page, formal_map)
                self.assert_visible_totals(formal_page, formal_map, VISIBLE_EXPANDED_NODES)
                self.assertEqual(formal_page.locator("#draft").inner_text(), "")
                self.assertFalse((self.documents["正式"]["directory"] / "DRAFT").exists())
                self.assertEqual(external_requests, [])
                self.assertEqual(formal_requests, [])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
