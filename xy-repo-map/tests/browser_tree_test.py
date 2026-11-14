import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from playwright.sync_api import sync_playwright

from map_fixture import COMMAND, feature, make_repo


class BrowserTreeTest(unittest.TestCase):
    def render(self, repository, temporary):
        output = Path(temporary) / "rendered"
        rendered = subprocess.run(
            ["python3", "-B", str(COMMAND), "render", "--repo", str(repository),
             "--out", str(output)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
        return output

    def test_tree_filters_decisions_and_fits_a_phone_viewport(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable")
        external_name = "external-" + "x" * 51
        alpha_source = "# one comment\n" + "".join(
            f"value_{index} = {index}\n" for index in range(12)
        )
        repository = make_repo(self, {
            "README.md": (
                f"See alpha/main.py and group. Claude Code and {external_name} are used here.\n"
            ),
            "alpha/main.py": alpha_source,
            "alpha/guide.md": "See group/ready.py for details.\n",
            "group/ready.py": "value = 2\n",
            "group/consumer.py": "import alpha.main\nimport group.ready\n",
            "group/orphan.py": "orphan = True\n",
            "group/tests/test_ready.py": "import group.ready\n",
        }, feature(
            "root", "", subrepositories=[],
            external_dependencies=["Claude Code", external_name],
            children=[
                feature("alpha", "alpha"),
                feature("group", "group", children=[
                    feature("ready", None, entries=["group/ready.py"]),
                    feature("consumer", None, entries=["group/consumer.py"], source="确认"),
                    feature("future", None, source="起草"),
                ]),
                feature("guide", None, entries=["README.md"]),
            ],
        ))
        with tempfile.TemporaryDirectory() as temporary:
            output = self.render(repository, temporary)
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

                group_section = row_for_title("Group").locator("xpath=ancestor::section[1]")
                group_summary = group_section.locator(".undivided")
                self.assertEqual(group_summary.count(), 1)
                self.assertTrue(group_summary.is_visible())
                row_for_title("Group").locator(".caret").click()
                self.assertFalse(group_summary.is_visible())
                row_for_title("Group").locator(".caret").click()
                self.assertTrue(group_summary.is_visible())

                with self.subTest(page_part="root rationale"):
                    root_why = page.locator("#page-why")
                    self.assertEqual(root_why.count(), 1)
                    if root_why.count():
                        self.assertIn(root_feature["why"]["text"], root_why.text_content())
                        self.assertEqual(
                            root_why.locator(".source-label").text_content(),
                            "你的原话",
                        )
                        self.assertIn("用户说过的话。", root_why.text_content())
                        self.assertIn("测试契约", root_why.text_content())
                by_title = {
                    feature_data["title"]: feature_id
                    for feature_id, feature_data in expected["features"].items()
                }
                guide = expected["features"][by_title["Guide"]]
                self.assertEqual(guide["uses"], [])
                self.assertEqual(guide["external_uses"], ["Claude Code", external_name])
                guide_uses = row_for_title("Guide").locator(".uses")
                self.assertEqual(guide_uses.locator(".uses-label").text_content(), "用到")
                self.assertEqual(
                    guide_uses.locator(".external-use").all_text_contents(),
                    ["Claude Code外部", f"{external_name}外部"],
                )
                external_chip = guide_uses.locator(".external-use").first
                self.assertEqual(external_chip.text_content(), "Claude Code外部")
                self.assertEqual(external_chip.evaluate("element => element.tagName"), "SPAN")
                self.assertFalse(external_chip.evaluate("element => Boolean(element.closest('button'))"))
                mention_chips = row_for_title("Alpha").locator(".uses .use-link")
                self.assertEqual(
                    mention_chips.all_text_contents(),
                    ["Group（group）提及", "Ready提及"],
                )
                for chip in mention_chips.all():
                    self.assertEqual(chip.locator(".mention").text_content(), "提及")
                consumer_uses = row_for_title("Consumer").locator(".uses .use-link").all_text_contents()
                self.assertEqual(consumer_uses, ["Ready", "Alpha（alpha）"])
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
                detail_external = panel.locator(".detail-list").first.locator(".external-use").first
                self.assertEqual(detail_external.text_content(), "Claude Code外部")
                self.assertEqual(detail_external.evaluate("element => element.tagName"), "SPAN")
                self.assertFalse(detail_external.evaluate("element => Boolean(element.closest('button'))"))

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

    def test_detail_panel_shows_items_at_phone_width(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable")
        items = [
            {
                "id": "reviewable",
                "text": "用户可以在手机上看到这条待确认的功能说明。",
                "status": "起草",
            },
            {
                "id": "confirmed",
                "text": "已确认的说明也会显示。",
                "status": "确认",
            },
        ]
        repository = make_repo(self, {"README.md": "Demo.\n"}, feature(
            "root", "", subrepositories=[], external_dependencies=[], children=[
                feature("details", None, source="确认", items=items),
            ],
        ))
        with tempfile.TemporaryDirectory() as temporary:
            output = self.render(repository, temporary)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=executable, headless=True,
                )
                try:
                    page = browser.new_page(viewport={"width": 390, "height": 844})
                    page.goto((output / "index.html").as_uri())
                    page.locator(".feature-title").click()
                    panel = page.locator(".detail-panel")
                    item_list = panel.locator(".items-list")
                    self.assertEqual(item_list.count(), 1)
                    self.assertEqual(
                        item_list.locator(".item-text").all_text_contents(),
                        [item["text"] for item in items],
                    )
                    self.assertEqual(
                        item_list.locator(".source-label.draft").all_text_contents(),
                        ["等你拍板"],
                    )
                    self.assertLessEqual(
                        page.evaluate("document.documentElement.scrollWidth"),
                        page.evaluate("window.innerWidth"),
                    )
                finally:
                    browser.close()
