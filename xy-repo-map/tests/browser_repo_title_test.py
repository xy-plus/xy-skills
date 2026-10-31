"""Browser checks for subrepository display titles."""
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
                self.assertEqual(self.card_title(page, "workspace.engine"),
                                 "引擎子仓（engine）")
                self.assertEqual(self.card_title(page, "workspace.solo"), "solo")
                self.assertEqual(map_document["nodes"]["workspace.engine"]["title"],
                                 "引擎子仓")
                self.assertEqual(map_document["nodes"]["workspace.solo"]["title"], "solo")

                page.goto(f"{self.base_url}/draft/index.html#%E9%92%BB%E5%85%A5/workspace",
                          wait_until="load")
                self.wait_for_view(page, "drill", "workspace")
                self.assertEqual(self.card_title(page, "workspace.engine"),
                                 "引擎子仓（engine）")
                self.assertEqual(self.card_title(page, "workspace.solo"), "solo")
                page.locator('.card[data-node-id="workspace.engine"]').click()
                self.wait_for_view(page, "drill", "workspace.engine")
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
