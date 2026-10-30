"""Browser-level navigation checks for child repository drill views."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.site import write_site


ZERO = {"code": 0, "docs": 0, "comments": 0, "tests": 0}


def make_documents(child_has_content):
    map_nodes = {
        "workspace": {"title": "工作区", "kind": "node", "parent": None,
                       "own": dict(ZERO), "total": dict(ZERO)},
        "workspace.engine": {"title": "引擎仓", "kind": "repo", "parent": "workspace",
                             "own": dict(ZERO), "total": dict(ZERO)},
        "engine": {"title": "子仓根", "kind": "node", "parent": "workspace.engine",
                   "own": dict(ZERO), "total": dict(ZERO)},
    }
    if child_has_content:
        map_nodes["engine.cache"] = {
            "title": "缓存", "kind": "node", "parent": "engine",
            "own": dict(ZERO), "total": dict(ZERO),
        }
    page_nodes = {
        element_id: {
            **node,
            "description": f"{node['title']} 描述",
            "coverage": "不适用",
        }
        for element_id, node in map_nodes.items()
    }
    placeholder_nodes = ["engine.cache"] if child_has_content else ["workspace.engine"]
    placeholder_breadcrumbs = ["workspace", "workspace.engine"]
    content_breadcrumbs = [*placeholder_breadcrumbs, "engine.cache"]
    page_data = {
        "mode": "草稿",
        "top_roots": ["workspace"],
        "nodes": page_nodes,
        "relations": [],
        "repo_roots": {"workspace.engine": "engine"},
        "drill_views": {
            "workspace": {"nodes": ["workspace.engine"], "relations": [],
                          "breadcrumbs": ["workspace"]},
            "workspace.engine": {"nodes": placeholder_nodes, "relations": [],
                                 "breadcrumbs": placeholder_breadcrumbs},
            "engine": {"nodes": placeholder_nodes, "relations": [],
                       "breadcrumbs": placeholder_breadcrumbs},
            **({"engine.cache": {"nodes": ["engine.cache"], "relations": [],
                                 "breadcrumbs": content_breadcrumbs}}
               if child_has_content else {}),
        },
    }
    return {"mode": "草稿", "nodes": map_nodes}, page_data


class ChildRepositoryNavigationTest(unittest.TestCase):
    def open_page(self, page, directory, child_has_content):
        map_data, page_data = make_documents(child_has_content)
        output = directory / ("populated" if child_has_content else "root-only")
        output.mkdir()
        write_site(map_data, page_data, output)
        page.add_init_script("""
          window.__cardClicks = [];
          const original = EventTarget.prototype.addEventListener;
          EventTarget.prototype.addEventListener = function(type, listener, options) {
            if (type === "click" && this.classList?.contains("card")) {
              window.__cardClicks.push(this.getAttribute("data-node-id"));
            }
            return original.call(this, type, listener, options);
          };
        """)
        page.goto((output / "index.html").as_uri() + "#%E9%92%BB%E5%85%A5/workspace",
                  wait_until="load")
        page.wait_for_function("document.querySelectorAll('.card').length > 0")
        return page

    def test_populated_placeholder_opens_child_content_without_extra_root_card(self):
        with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=os.environ["XY_REPO_MAP_CHROMIUM"])
            try:
                page = self.open_page(browser.new_page(), Path(temporary), True)
                self.assertIn("workspace.engine", page.evaluate("window.__cardClicks"))
                page.locator('[data-node-id="workspace.engine"]').click()
                page.wait_for_function("location.hash.includes('workspace.engine')")
                visible = page.locator(".card").evaluate_all(
                    "cards => cards.map(card => card.getAttribute('data-node-id'))")
                self.assertEqual(visible, ["engine.cache"])
                self.assertEqual(page.locator("#crumbs button").all_text_contents(),
                                 ["工作区", "引擎仓"])
            finally:
                browser.close()

    def test_root_only_placeholder_has_no_click_registration_or_navigation(self):
        with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=os.environ["XY_REPO_MAP_CHROMIUM"])
            try:
                page = self.open_page(browser.new_page(), Path(temporary), False)
                self.assertNotIn("workspace.engine", page.evaluate("window.__cardClicks"))
                placeholder = page.locator('[data-node-id="workspace.engine"]')
                self.assertNotIn("clickable", placeholder.get_attribute("class"))
                self.assertEqual(
                    placeholder.locator(".card-shape").get_attribute("stroke-dasharray"), "none")
                self.assertNotEqual(
                    placeholder.evaluate("element => getComputedStyle(element).cursor"), "pointer")
                current_hash = page.evaluate("location.hash")
                placeholder.click()
                self.assertEqual(page.evaluate("location.hash"), current_hash)
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
