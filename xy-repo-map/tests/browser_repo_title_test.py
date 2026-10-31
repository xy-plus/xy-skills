"""Browser checks for subrepository display titles."""
import os
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright

from browser_child_repo_test import ParentChildBrowserTestSupport


class RepositoryTitleBrowserAcceptanceTest(ParentChildBrowserTestSupport, unittest.TestCase):
    def card_title(self, page, element_id):
        return page.locator(
            f'.card[data-node-id="{element_id}"] .card-title').evaluate(
                "element => element.textContent")

    def test_repository_names_appear_on_cards_and_breadcrumbs(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(f"{self.base_url}/draft/index.html#%E5%B1%95%E5%BC%80",
                          wait_until="load")
                self.wait_for_view(page, "expand", None)
                map_document = self.map_data("draft")
                page_document = self.page_data(page)

                self.assertEqual(page_document["repo_names"], {
                    "workspace.engine": "engine", "workspace.solo": "solo",
                })
                self.assertEqual(self.card_title(page, "workspace.engine"),
                                 "引擎子仓（engine）")
                self.assertEqual(self.card_title(page, "workspace.solo"), "solo")
                self.assertEqual(map_document["nodes"]["workspace.engine"]["title"],
                                 "引擎子仓")
                self.assertEqual(page_document["nodes"]["workspace.engine"]["title"],
                                 "引擎子仓")
                self.assertEqual(map_document["nodes"]["workspace.solo"]["title"], "solo")
                self.assertEqual(page_document["nodes"]["workspace.solo"]["title"], "solo")

                screenshot_directory = os.environ.get("XY_REPO_MAP_SCREENSHOT_DIR")
                if screenshot_directory:
                    screenshot_path = Path(screenshot_directory)
                    screenshot_path.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(screenshot_path / "父子仓全部展开-1440x900.png"))

                page.goto(f"{self.base_url}/draft/index.html#%E9%92%BB%E5%85%A5/workspace",
                          wait_until="load")
                self.wait_for_view(page, "drill", "workspace")
                self.assertEqual(self.card_title(page, "workspace.engine"),
                                 "引擎子仓（engine）")
                self.assertEqual(self.card_title(page, "workspace.solo"), "solo")
                page.locator('.card[data-node-id="workspace.engine"]').click()
                self.wait_for_view(page, "drill", "workspace.engine")
                self.assertEqual(page.locator("#crumbs button").all_text_contents(),
                                 ["工作区", "引擎子仓（engine）"])
                if screenshot_directory:
                    page.screenshot(path=str(
                        Path(screenshot_directory) / "父子仓钻入引擎-1440x900.png"))
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
