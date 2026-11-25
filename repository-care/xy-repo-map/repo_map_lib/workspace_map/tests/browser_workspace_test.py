import json
import os
import unittest

from playwright.sync_api import sync_playwright

from pathlib import Path
import sys
REPOSITORY_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists())
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "tests"))
from map_fixture import make_workspace, run_map_command


class BrowserWorkspaceTest(unittest.TestCase):
    def test_cross_repository_use_opens_the_target_repository(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable")
        workspace = make_workspace(self, ("sub-b",), ("sub-a",))
        output = workspace.parent / "rendered"
        rendered = run_map_command("render-workspace", workspace, output)
        self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=executable, headless=True)
            page = browser.new_page()
            page.goto((output / "index.html").as_uri())
            self.assertEqual(
                page.get_by_role("button", name="图", exact=True).get_attribute(
                    "aria-pressed"
                ),
                "true",
            )
            self.assertTrue(page.locator("#picture-view").is_visible())
            self.assertEqual(
                page.locator("#picture-note").text_content(),
                "箭头从上级指向子功能；圆越大，行数越多（含子功能）；颜色区分各仓。",
            )
            map_data = json.loads(page.locator("#map-data").text_content())
            features = map_data["features"]
            repository_roots = [
                feature_id
                for feature_id in features[map_data["root"]]["children"]
                if "/" in feature_id
            ]
            self.assertEqual(len(repository_roots), 2)
            root_id = map_data["root"]
            root_color = page.locator(
                f'.picture-node[data-feature-id="{root_id}"] circle'
            ).evaluate("circle => getComputedStyle(circle).fill")
            repository_colors = [
                page.locator(
                    f'.picture-node[data-feature-id="{feature_id}"] circle'
                ).evaluate("circle => getComputedStyle(circle).fill")
                for feature_id in repository_roots
            ]
            self.assertEqual(len({root_color, *repository_colors}), 3)

            positions = page.locator(".picture-node").evaluate_all(
                "nodes => nodes.map(node => ({id: node.dataset.featureId, "
                "x: node.transform.baseVal.consolidate().matrix.e}))"
            )
            root_x = next(node["x"] for node in positions if node["id"] == root_id)
            for repository_root in repository_roots:
                repository = repository_root.rsplit("/", 1)[0]
                repository_sides = {
                    node["x"] < root_x
                    for node in positions
                    if node["id"].startswith(f"{repository}/")
                }
                with self.subTest(repository=repository):
                    self.assertEqual(len(repository_sides), 1)
            page.get_by_role("button", name="Root（sub-a）", exact=True).click()
            self.assertEqual(
                page.locator(".detail-panel h2").text_content(), "Root（sub-a）"
            )
            page.get_by_role("button", name="关闭详情").click()
            page.get_by_role("button", name="文字", exact=True).click()
            sub_a = next(row for row in page.locator(".feature-row").all()
                         if row.locator(".directory-name").text_content() == "（sub-a）")
            sub_a.locator(".use-link").click()
            self.assertEqual(page.locator(".detail-panel h2").text_content(), "Root（sub-b）")
            browser.close()
