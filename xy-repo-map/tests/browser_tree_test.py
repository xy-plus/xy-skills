"""Check the rendered feature tree in a narrow, offline Chromium page."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from playwright.sync_api import sync_playwright

from map_fixture import COMMAND, feature, make_repo


class BrowserTreeTest(unittest.TestCase):
    def test_tree_filters_decisions_and_fits_a_phone_viewport(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable")
        repository = make_repo(self, {
            "README.md": "Demo.\n",
            "alpha/main.py": "value = 1\n",
            "group/ready.py": "value = 2\n",
        }, feature("root", "", children=[
            feature("alpha", "alpha"),
            feature("group", "group", children=[
                feature("ready", None, entries=["group/ready.py"]),
                feature("future", None, source="起草"),
            ]),
        ]))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "rendered"
            rendered = subprocess.run(
                ["python3", "-B", str(COMMAND), "render", "--repo", str(repository),
                 "--out", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
            expected = json.loads((output / "map-data.json").read_text(encoding="utf-8"))

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=executable, headless=True)
                page = browser.new_page(viewport={"width": 390, "height": 844})
                script_errors = []
                page.on("pageerror", lambda error: script_errors.append(str(error)))
                page.goto((output / "index.html").as_uri())

                row_ids = page.locator(".feature-row").evaluate_all(
                    "rows => rows.map(row => row.dataset.featureId)"
                )
                self.assertNotIn(expected["root"], row_ids)
                self.assertEqual(len(row_ids), len(expected["features"]) - 1, script_errors)
                for feature_id, feature_data in expected["features"].items():
                    if feature_id == expected["root"] or not any(feature_data["lines"]["total"].values()):
                        continue
                    row = page.locator(f'.feature-row[data-feature-id="{feature_id}"]')
                    widths = row.locator(".fill i").evaluate_all(
                        "parts => parts.map(part => part.getBoundingClientRect().width)"
                    )
                    self.assertTrue(any(width > 0 for width in widths), feature_id)
                page.locator(".feature-row").first.click()
                panel_style = page.locator(".detail-panel").evaluate(
                    "element => { const style = getComputedStyle(element); return {"
                    "borderTopWidth: style.borderTopWidth, borderTopColor: style.borderTopColor, "
                    "borderTopLeftRadius: style.borderTopLeftRadius, maxHeight: style.maxHeight }; }"
                )
                self.assertEqual(panel_style["borderTopWidth"], "1px")
                self.assertEqual(panel_style["borderTopColor"], "rgb(221, 227, 233)")
                self.assertEqual(panel_style["borderTopLeftRadius"], "16px")
                panel_limit = float(panel_style["maxHeight"].removesuffix("px"))
                self.assertLessEqual(panel_limit, 0.45 * 844)
                embedded = page.evaluate(
                    "JSON.parse(document.querySelector('#map-data').textContent)"
                )
                for item in embedded["features"].values():
                    self.assertNotIn("files", item)
                self.assertLessEqual(
                    page.evaluate("document.documentElement.scrollWidth"),
                    page.evaluate("window.innerWidth"),
                )

                page.locator("#decision-filter").click()
                visible = page.locator(".feature-row:not([hidden])").evaluate_all(
                    "rows => rows.map(row => row.dataset.featureId)"
                )
                self.assertEqual(visible, ["future"])
                page.locator("#decision-filter").click()
                self.assertEqual(script_errors, [])
                browser.close()


if __name__ == "__main__":
    unittest.main()
