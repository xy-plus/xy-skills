"""Exercise composed workspace links in the offline feature-tree page."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from playwright.sync_api import sync_playwright

from map_fixture import COMMAND, make_workspace


class BrowserWorkspaceTest(unittest.TestCase):
    def test_workspace_expands_children_and_uses_distinct_cross_repository_names(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable")
        workspace = make_workspace(self, ("sub-b",), ("sub-a",))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "rendered"
            rendered = subprocess.run(
                ["python3", "-B", str(COMMAND), "render-workspace",
                 "--repo", str(workspace), "--out", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
            expected = json.loads((output / "map-data.json").read_text(encoding="utf-8"))
            self.assertEqual(
                expected["features"]["sub-a/root"]["uses"],
                [{"feature": "sub-b/root", "by": "mention"}],
            )

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=executable, headless=True)
                page = browser.new_page(viewport={"width": 900, "height": 900})
                page.goto((output / "index.html").as_uri())

                def row_for_repository(name):
                    rows = [
                        row for row in page.locator(".feature-row").all()
                        if row.locator(".feature-title .directory-name").text_content()
                        == f"（{name}）"
                    ]
                    self.assertEqual(len(rows), 1, name)
                    return rows[0]

                sub_a = row_for_repository("sub-a")
                sub_b = row_for_repository("sub-b")
                sub_a_section = sub_a.locator("xpath=ancestor::section[1]")
                sub_b_section = sub_b.locator("xpath=ancestor::section[1]")
                self.assertEqual(sub_a.locator(".feature-title .title").text_content(), "Root")
                self.assertEqual(sub_b.locator(".feature-title .title").text_content(), "Root")
                self.assertEqual(
                    sub_a.locator(".uses .use-link").all_text_contents(),
                    ["Root（sub-b）提及"],
                )
                self.assertEqual(
                    sub_b.locator(".uses .use-link").all_text_contents(),
                    ["Root（sub-a）提及"],
                )

                page.locator("#depth-minus").click()
                self.assertFalse(sub_a_section.locator(".feature-children").is_visible())
                self.assertFalse(sub_b_section.locator(".feature-children").is_visible())
                sub_a.locator(".caret").click()
                sub_b.locator(".caret").click()
                self.assertTrue(sub_a_section.locator(".feature-children").is_visible())
                self.assertTrue(sub_b_section.locator(".feature-children").is_visible())
                visible_titles = page.locator(
                    ".feature-row:visible .feature-title .title"
                ).all_text_contents()
                self.assertEqual(visible_titles.count("Part"), 2)

                sub_a_part = sub_a_section.locator(".feature-children .feature-row")
                sub_a_part.locator(".feature-title").click()
                detail = page.locator(".detail-panel")
                self.assertTrue(detail.is_visible())
                self.assertEqual(detail.locator("h2").text_content(), "Part（lib）")
                self.assertIn("Root（sub-a）", detail.inner_text())
                detail.locator(".close-detail").click()

                sub_a.locator(".uses .use-link").click()
                detail = page.locator(".detail-panel")
                self.assertTrue(detail.is_visible())
                self.assertEqual(detail.locator("h2").text_content(), "Root（sub-b）")
                browser.close()


if __name__ == "__main__":
    unittest.main()
