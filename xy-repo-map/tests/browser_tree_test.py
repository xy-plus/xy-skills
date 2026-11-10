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
        alpha_source = "# one comment\n" + "".join(
            f"value_{index} = {index}\n" for index in range(12)
        )
        repository = make_repo(self, {
            "README.md": "See alpha/main.py and group.\n",
            "alpha/main.py": alpha_source,
            "alpha/guide.md": "See group/ready.py for details.\n",
            "group/ready.py": "value = 2\n",
            "group/consumer.py": "import alpha.main\nimport group.ready\n",
            "group/orphan.py": "orphan = True\n",
            "group/tests/test_ready.py": "import group.ready\n",
        }, feature("root", "", children=[
            feature("alpha", "alpha"),
            feature("group", "group", children=[
                feature("ready", None, entries=["group/ready.py"]),
                feature("consumer", None, entries=["group/consumer.py"], source="确认"),
                feature("future", None, source="起草"),
            ]),
            feature("guide", None, entries=["README.md"]),
        ], source="起草"))
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

                root_feature = expected["features"][expected["root"]]
                row_titles = page.locator(".feature-row .feature-title .title").all_text_contents()
                self.assertNotIn(root_feature["title"], row_titles)
                self.assertEqual(len(row_titles), len(expected["features"]) - 1, script_errors)

                def row_for_title(title):
                    matches = [
                        row for row in page.locator(".feature-row").all()
                        if row.locator(".feature-title .title").text_content() == title
                    ]
                    self.assertEqual(len(matches), 1, title)
                    return matches[0]

                with self.subTest(page_part="root rationale"):
                    root_why = page.locator("#page-why")
                    self.assertEqual(root_why.count(), 1)
                    if root_why.count():
                        self.assertIn(root_feature["why"]["text"], root_why.text_content())
                        self.assertEqual(
                            root_why.locator(".source-label").text_content(),
                            "AI 起草 · 等你拍板",
                        )
                by_title = {
                    feature_data["title"]: feature_id
                    for feature_id, feature_data in expected["features"].items()
                }
                guide = expected["features"][by_title["Guide"]]
                self.assertEqual(guide["uses"], [])
                mention_chips = row_for_title("Alpha").locator(".uses .use-link")
                self.assertEqual(mention_chips.all_text_contents(), ["Group提及", "Ready提及"])
                for chip in mention_chips.all():
                    self.assertEqual(chip.locator(".mention").text_content(), "提及")
                consumer_uses = row_for_title("Consumer").locator(".uses .use-link").all_text_contents()
                self.assertEqual(consumer_uses, ["Ready", "Alpha"])
                consumer_row = row_for_title("Consumer")
                why_row = consumer_row.locator(".why")
                self.assertEqual(why_row.evaluate("element => element.tagName"), "P")
                self.assertEqual(why_row.locator("strong").text_content(), "为什么：")
                self.assertEqual(why_row.locator(".source-label").count(), 1)
                line_totals = expected["features"][expected["root"]]["lines"]["total"]
                line_total = sum(line_totals.values())
                bar_width = page.locator("#summary-bar").evaluate(
                    "element => element.getBoundingClientRect().width"
                )
                for key, count in line_totals.items():
                    if not count:
                        continue
                    segment = page.locator(f"#summary-bar > .{key}")
                    segment_width = segment.evaluate(
                        "element => element.getBoundingClientRect().width"
                    )
                    expected_width = bar_width * count / line_total
                    self.assertLessEqual(abs(segment_width - expected_width), 2, key)
                    if count / line_total < 0.12:
                        self.assertEqual(segment.inner_text(), "", key)
                for feature_id, feature_data in expected["features"].items():
                    if feature_id == expected["root"] or not any(feature_data["lines"]["total"].values()):
                        continue
                    row = row_for_title(feature_data["title"])
                    widths = row.locator(".fill i").evaluate_all(
                        "parts => parts.map(part => part.getBoundingClientRect().width)"
                    )
                    self.assertTrue(any(width > 0 for width in widths), feature_id)
                page.locator(".feature-row").first.click()
                panel_limit = page.locator(".detail-panel").evaluate(
                    "element => parseFloat(getComputedStyle(element).maxHeight)"
                )
                self.assertLessEqual(panel_limit, 0.45 * 844)
                self.assertLessEqual(
                    page.evaluate("document.documentElement.scrollWidth"),
                    page.evaluate("window.innerWidth"),
                )

                page.locator("#decision-filter").click()
                visible_titles = page.locator(
                    ".feature-row:not([hidden]) .feature-title .title"
                ).all_text_contents()
                features = expected["features"]

                def has_decision_in_branch(feature_id):
                    feature_data = features[feature_id]
                    return "等你拍板" in feature_data["markers"] or any(
                        has_decision_in_branch(child_id)
                        for child_id in feature_data["children"]
                    )

                expected_visible_titles = {
                    feature_data["title"]
                    for feature_id, feature_data in features.items()
                    if feature_id != expected["root"]
                    and has_decision_in_branch(feature_id)
                }
                with self.subTest(page_part="decision rows and ancestors"):
                    self.assertEqual(set(visible_titles), expected_visible_titles)
                with self.subTest(page_part="undivided code summary"):
                    self.assertEqual(page.locator(".undivided:visible").count(), 0)
                page.locator("#decision-filter").click()
                self.assertEqual(script_errors, [])
                with self.subTest(page_part="root detail and close"):
                    page.locator("#page-title").click()
                    root_panel = page.locator(".detail-panel")
                    self.assertTrue(root_panel.is_visible())
                    if root_panel.is_visible():
                        self.assertEqual(
                            root_panel.locator("h2").text_content(),
                            f'{root_feature["title"]}（{root_feature["directory_name"]}）',
                        )
                        self.assertIn("上级路径：无", root_panel.inner_text())
                        root_panel.locator(".close-detail").click()
                        self.assertFalse(root_panel.is_visible())
                    self.assertEqual(script_errors, [])
                row_for_title("Guide").locator(".feature-title").click()
                panel = page.locator(".detail-panel")
                headings = panel.locator("h3").all_text_contents()
                self.assertNotIn("出处", headings)
                self.assertEqual(panel.locator(".why-detail-body .source-label").text_content(), "你的原话")
                citation = panel.locator("blockquote")
                self.assertIn("用户说过的话。", citation.inner_text())
                self.assertIn("测试契约", citation.inner_text())

                row_for_title("Consumer").locator(".feature-title").click()
                panel = page.locator(".detail-panel")
                self.assertNotIn("出处", panel.locator("h3").all_text_contents())
                self.assertEqual(panel.locator(".why-detail-body .source-label").text_content(), "你确认过")
                self.assertIn("本次回答", panel.inner_text())
                self.assertEqual(panel.locator("blockquote").count(), 0)

                row_for_title("Future").locator(".feature-title").click()
                panel = page.locator(".detail-panel")
                self.assertNotIn("出处", panel.locator("h3").all_text_contents())
                self.assertEqual(panel.locator(".why-detail-body .source-label").text_content(), "AI 起草 · 等你拍板")
                self.assertNotIn("来源：", panel.inner_text())
                browser.close()


if __name__ == "__main__":
    unittest.main()
