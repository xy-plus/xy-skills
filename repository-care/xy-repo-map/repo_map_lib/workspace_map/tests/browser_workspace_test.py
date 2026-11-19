import os
import subprocess
import unittest

from playwright.sync_api import sync_playwright

from pathlib import Path
import sys
REPOSITORY_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists())
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "tests"))
from map_fixture import COMMAND, make_workspace


class BrowserWorkspaceTest(unittest.TestCase):
    def test_cross_repository_use_opens_the_target_repository(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable")
        workspace = make_workspace(self, ("sub-b",), ("sub-a",))
        output = workspace.parent / "rendered"
        rendered = subprocess.run(
            ["python3", "-B", str(COMMAND), "render-workspace", "--repo", str(workspace),
             "--out", str(output)], capture_output=True, text=True, check=False,
        )
        self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=executable, headless=True)
            page = browser.new_page()
            page.goto((output / "index.html").as_uri())
            sub_a = next(row for row in page.locator(".feature-row").all()
                         if row.locator(".directory-name").text_content() == "（sub-a）")
            sub_a.locator(".use-link").click()
            self.assertEqual(page.locator(".detail-panel h2").text_content(), "Root（sub-b）")
            browser.close()
