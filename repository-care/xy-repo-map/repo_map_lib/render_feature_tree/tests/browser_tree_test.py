import json
import os
import unittest

from playwright.sync_api import sync_playwright

from pathlib import Path
import sys

REPOSITORY_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "tests"))
from map_fixture import (
    feature,
    make_repo,
    root_feature as make_root_feature,
    run_map_command,
)


def _row_for_title(test_case, page, title):
    matches = [
        row
        for row in page.locator(".feature-row").all()
        if row.locator(".feature-title .title").text_content() == title
    ]
    test_case.assertEqual(len(matches), 1, title)
    return matches[0]


def _make_rendered_tree(test_case):
    external_name = "external-" + "x" * 51
    payload = '</script><img src=x onerror="alert(1)">'
    alpha_source = "# one comment\n" + "".join(
        f"value_{index} = {index}\n" for index in range(12)
    )
    future = feature(
        "future",
        "group/future",
        source="起草",
        items=[
            {"id": "draft", "text": "待确认的条目。", "status": "起草"},
            {"id": "confirmed", "text": "已确认的条目。", "status": "确认"},
        ],
        children=[],
    )
    future["what"] = payload
    repository = make_repo(
        test_case,
        {
            "README.md": "Root overview.\n",
            "guide/guide.md": f"Claude Code and {external_name} are used here.\n",
            "alpha/main.py": alpha_source,
            "alpha/guide.md": "See group/ready/main.py for details.\n",
            "group/ready/main.py": "value = 2\n",
            "group/consumer/main.py": "import alpha.main\nimport group.ready.main\n",
            "group/orphan.py": "orphan = True\n",
            "group/tests/test_ready.py": "import group.ready\n",
            "group/future/note.py": "value = 3\n",
        },
        make_root_feature(
            "root",
            "",
            subrepositories=[],
            external_dependencies=["Claude Code", external_name],
            children=[
                feature("alpha", "alpha", children=[], source="原话", items=[]),
                feature(
                    "group",
                    "group",
                    children=[
                        feature(
                            "ready",
                            "group/ready",
                            children=[],
                            source="原话",
                            items=[],
                        ),
                        feature(
                            "consumer",
                            "group/consumer",
                            source="确认",
                            children=[],
                            items=[],
                        ),
                        future,
                    ],
                    source="原话",
                    items=[],
                ),
                feature("guide", "guide", children=[], source="原话", items=[]),
            ],
            source="原话",
            items=[],
        ),
    )
    (repository / "repo-map/skip.json").write_text(
        '["group/future/note.py"]\n',
        encoding="utf-8",
    )
    output = repository.parent / "rendered"
    rendered = run_map_command("render", repository, output)
    test_case.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
    expected = json.loads((output / "map-data.json").read_text(encoding="utf-8"))
    test_case.assertEqual(
        expected["features"]["future"]["files"], ["group/future/note.py"]
    )
    test_case.assertEqual(
        expected["features"]["future"]["lines"]["total"],
        {"code": 0, "docs": 0, "comments": 0, "tests": 0},
    )
    return output, expected, payload, external_name


def _make_greedy_layout_rendered_tree(test_case):
    files = {"README.md": "Root overview.\n"}
    children = []
    for branch_index, leaf_count in enumerate((3, 8, 7, 6, 5, 4)):
        branch_id = f"branch-{branch_index}"
        leaves = []
        for leaf_index in range(leaf_count):
            directory = f"{branch_id}/leaf-{leaf_index}"
            files[f"{directory}/main.py"] = "value = 1\n"
            leaves.append(
                feature(
                    f"{branch_id}-leaf-{leaf_index}",
                    directory,
                    source="原话",
                    children=[],
                    items=[],
                )
            )
        children.append(
            feature(
                branch_id,
                branch_id,
                source="原话",
                children=leaves,
                items=[],
            )
        )
    repository = make_repo(
        test_case,
        files,
        make_root_feature(
            "root",
            "",
            subrepositories=[],
            external_dependencies=[],
            children=children,
            source="原话",
            items=[],
        ),
    )
    output = repository.parent / "rendered"
    rendered = run_map_command("render", repository, output)
    test_case.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
    expected = json.loads((output / "map-data.json").read_text(encoding="utf-8"))
    return output, expected


def _assert_default_picture_view(test_case, page):
    picture_button = page.get_by_role("button", name="图", exact=True)
    text_button = page.get_by_role("button", name="文字", exact=True)
    test_case.assertEqual(picture_button.get_attribute("aria-pressed"), "true")
    test_case.assertEqual(text_button.get_attribute("aria-pressed"), "false")
    test_case.assertTrue(page.locator("#picture-view").is_visible())
    test_case.assertFalse(page.locator("#text-view").is_visible())


def _assert_picture_note(test_case, page):
    test_case.assertEqual(
        page.locator("#picture-note").text_content(),
        "箭头从上级指向子功能；圆越大，行数越多（含子功能）；颜色区分各个顶层分支。",
    )


def _assert_picture_edges_are_parent_child_only(test_case, page, expected):
    test_case.assertEqual(
        page.locator("#picture-svg .picture-node").count(),
        len(expected["features"]),
    )
    edges = page.locator("#picture-svg .tree-edge")
    test_case.assertEqual(edges.count(), len(expected["features"]) - 1)
    test_case.assertGreater(
        sum(bool(feature["uses"]) for feature in expected["features"].values()), 0
    )
    test_case.assertGreater(page.locator("#picture-svg marker").count(), 0)
    marker_colors = edges.evaluate_all(
        "edges => edges.map(edge => { "
        "const id = edge.getAttribute('marker-end').match(/#([^)]*)/)[1]; "
        "return {stroke: getComputedStyle(edge).stroke, "
        "fill: getComputedStyle(document.querySelector(`#${id} path`)).fill}; "
        "})"
    )
    for colors in marker_colors:
        test_case.assertEqual(colors["fill"], colors["stroke"])


def _assert_picture_labels_and_circles_do_not_overlap(test_case, page):
    svg = page.locator("#picture-svg")
    label_boxes = svg.locator(".picture-node text").evaluate_all(
        "labels => labels.map(label => { const box = label.getBoundingClientRect(); "
        "return {left: box.left, right: box.right, top: box.top, bottom: box.bottom}; })"
    )
    for index, left in enumerate(label_boxes):
        for right in label_boxes[index + 1 :]:
            test_case.assertFalse(
                left["left"] < right["right"]
                and right["left"] < left["right"]
                and left["top"] < right["bottom"]
                and right["top"] < left["bottom"],
                f"picture labels overlap: {left} and {right}",
            )
    circle_boxes = svg.locator(".picture-node circle").evaluate_all(
        "circles => circles.map(circle => { const box = circle.getBoundingClientRect(); "
        "return {left: box.left, right: box.right, top: box.top, bottom: box.bottom}; })"
    )
    for index, left in enumerate(circle_boxes):
        for right in circle_boxes[index + 1 :]:
            test_case.assertFalse(
                left["left"] < right["right"]
                and right["left"] < left["right"]
                and left["top"] < right["bottom"]
                and right["top"] < left["bottom"],
                f"picture circles overlap: {left} and {right}",
            )


def _assert_picture_node_opens_details_and_tracks_selection(test_case, page):
    original_url = page.url
    page.get_by_role("button", name="Guide（guide）", exact=True).click()
    panel = page.locator("#detail-panel")
    test_case.assertTrue(panel.is_visible())
    test_case.assertEqual(panel.locator("h2").text_content(), "Guide（guide）")
    test_case.assertEqual(page.url, original_url)
    selected = page.locator('.picture-node[data-feature-id="guide"]')
    test_case.assertTrue(
        selected.evaluate("node => node.classList.contains('selected')")
    )
    panel.get_by_role("button", name="关闭详情").click()
    test_case.assertFalse(
        selected.evaluate("node => node.classList.contains('selected')")
    )


def _assert_picture_nodes_support_keyboard(test_case, page):
    panel = page.locator("#detail-panel")
    guide_button = page.get_by_role("button", name="Guide（guide）", exact=True)
    guide_button.focus()
    page.keyboard.press("Enter")
    test_case.assertTrue(panel.is_visible())
    panel.get_by_role("button", name="关闭详情").click()
    guide_button.focus()
    page.keyboard.press("Space")
    test_case.assertTrue(panel.is_visible())
    panel.get_by_role("button", name="关闭详情").click()


def _assert_related_feature_selects_picture_node(test_case, page):
    panel = page.locator("#detail-panel")
    page.get_by_role("button", name="Consumer（consumer）", exact=True).click()
    panel.locator(".detail-list").first.get_by_role(
        "button", name="Ready（ready）", exact=True
    ).click()
    test_case.assertEqual(panel.locator("h2").text_content(), "Ready（ready）")
    test_case.assertTrue(
        page.locator('.picture-node[data-feature-id="ready"]').evaluate(
            "node => node.classList.contains('selected')"
        )
    )
    panel.get_by_role("button", name="关闭详情").click()


def _assert_view_switching(test_case, page):
    picture_button = page.get_by_role("button", name="图", exact=True)
    text_button = page.get_by_role("button", name="文字", exact=True)
    text_button.click()
    test_case.assertTrue(page.locator("#text-view").is_visible())
    test_case.assertFalse(page.locator("#picture-view").is_visible())
    picture_button.click()
    test_case.assertTrue(page.locator("#picture-view").is_visible())
    text_button.click()


def _assert_picture_zoom_and_decision_filter(test_case, page):
    page.get_by_role("button", name="图", exact=True).click()
    svg = page.locator("#picture-svg")
    starting_width = svg.evaluate("element => element.getBoundingClientRect().width")
    page.get_by_role("button", name="放大", exact=True).click()
    larger_width = svg.evaluate("element => element.getBoundingClientRect().width")
    test_case.assertGreater(larger_width, starting_width)
    page.get_by_role("button", name="缩小", exact=True).click()
    smaller_width = svg.evaluate("element => element.getBoundingClientRect().width")
    test_case.assertLess(smaller_width, larger_width)
    page.get_by_role("button", name="全图", exact=True).click()
    fitted_width = svg.evaluate("element => element.getBoundingClientRect().width")
    natural_width = float(svg.get_attribute("width"))
    test_case.assertAlmostEqual(fitted_width, starting_width, delta=1)
    for _ in range(8):
        page.get_by_role("button", name="放大", exact=True).click()
    maximum_width = svg.evaluate("element => element.getBoundingClientRect().width")
    test_case.assertLessEqual(maximum_width, natural_width * 2 + 1)
    for _ in range(20):
        page.get_by_role("button", name="缩小", exact=True).click()
    minimum_width = svg.evaluate("element => element.getBoundingClientRect().width")
    test_case.assertGreaterEqual(minimum_width, fitted_width - 1)
    test_case.assertLessEqual(
        page.evaluate("document.documentElement.scrollWidth"),
        page.evaluate("window.innerWidth"),
    )

    page.locator("#decision-filter").click()
    test_case.assertTrue(page.locator("#text-view").is_visible())
    test_case.assertEqual(
        page.locator("#decision-filter").get_attribute("aria-pressed"), "true"
    )
    test_case.assertNotIn(
        "Alpha",
        page.locator(
            ".feature-row:not([hidden]) .feature-title .title"
        ).all_text_contents(),
    )
    page.get_by_role("button", name="图", exact=True).click()
    test_case.assertEqual(
        page.locator("#decision-filter").get_attribute("aria-pressed"), "false"
    )
    page.get_by_role("button", name="文字", exact=True).click()
    test_case.assertEqual(
        page.locator("#decision-filter").get_attribute("aria-pressed"), "false"
    )


def _assert_theme_and_tree_rows(test_case, page, expected, payload):
    original_dark_preference = page.evaluate(
        "matchMedia('(prefers-color-scheme: dark)').matches"
    )
    page.emulate_media(color_scheme="light")
    light_background = page.locator("body").evaluate(
        "element => getComputedStyle(element).backgroundColor"
    )
    page.emulate_media(color_scheme="dark")
    dark_background = page.locator("body").evaluate(
        "element => getComputedStyle(element).backgroundColor"
    )
    test_case.assertNotEqual(light_background, dark_background)
    page.emulate_media(color_scheme="dark" if original_dark_preference else "light")

    future_row = _row_for_title(test_case, page, "Future")
    test_case.assertEqual(future_row.locator(".what").text_content(), payload)
    test_case.assertEqual(page.locator("img").count(), 0)
    root = expected["features"][expected["root"]]
    titles = page.locator(".feature-row .feature-title .title").all_text_contents()
    test_case.assertNotIn(root["title"], titles)
    test_case.assertEqual(len(titles), len(expected["features"]) - 1)

    group_row = _row_for_title(test_case, page, "Group")
    group_section = group_row.locator("xpath=ancestor::section[1]")
    group_summary = group_section.locator(".own-summary")
    group_toggle = group_row.locator(".caret")
    test_case.assertEqual(
        group_toggle.get_attribute("aria-label"), "展开或收起Group（group）"
    )
    test_case.assertEqual(group_toggle.get_attribute("aria-expanded"), "true")
    test_case.assertTrue(group_summary.is_visible())
    test_case.assertEqual(
        group_summary.locator("span").first.text_content(), "本级自己的部分"
    )
    group_toggle.click()
    test_case.assertEqual(group_toggle.get_attribute("aria-expanded"), "false")
    test_case.assertFalse(group_summary.is_visible())
    group_toggle.click()
    test_case.assertTrue(group_summary.is_visible())


def _assert_root_rationale(test_case, page, root_feature):
    root_why = page.locator("#page-why")
    test_case.assertEqual(root_why.count(), 1)
    test_case.assertIn(root_feature["why"]["text"], root_why.text_content())
    test_case.assertEqual(root_why.locator(".source-label").text_content(), "你的原话")
    test_case.assertIn("用户说过的话。", root_why.text_content())
    test_case.assertIn("测试契约", root_why.text_content())


def _assert_usage_relations(test_case, page, expected):
    row = lambda title: _row_for_title(test_case, page, title)
    by_title = {
        data["title"]: feature_id for feature_id, data in expected["features"].items()
    }
    guide = expected["features"][by_title["Guide"]]
    test_case.assertEqual(guide["uses"], [])
    test_case.assertEqual(
        guide["external_uses"], ["Claude Code", "external-" + "x" * 51]
    )
    guide_uses = row("Guide").locator(".uses")
    test_case.assertEqual(guide_uses.locator(".uses-label").text_content(), "用到")
    test_case.assertEqual(
        guide_uses.locator(".external-use").all_text_contents(),
        ["Claude Code外部", f"{'external-' + 'x' * 51}外部"],
    )
    external_chip = guide_uses.locator(".external-use").first
    test_case.assertEqual(external_chip.text_content(), "Claude Code外部")
    test_case.assertEqual(external_chip.evaluate("element => element.tagName"), "SPAN")
    test_case.assertFalse(
        external_chip.evaluate("element => Boolean(element.closest('button'))")
    )
    mention_chips = row("Alpha").locator(".uses .use-link")
    test_case.assertEqual(mention_chips.all_text_contents(), ["Ready（ready）提及"])
    for chip in mention_chips.all():
        test_case.assertEqual(chip.locator(".mention").text_content(), "提及")
    consumer_uses = row("Consumer").locator(".uses .use-link").all_text_contents()
    test_case.assertEqual(consumer_uses, ["Ready（ready）", "Alpha（alpha）"])
    ready_incoming = row("Ready").locator(".incoming .incoming-link")
    test_case.assertEqual(
        ready_incoming.all_text_contents(),
        ["Alpha（alpha）提及", "Consumer（consumer）"],
    )
    alpha_incoming = row("Alpha").locator(".incoming .incoming-link")
    test_case.assertEqual(alpha_incoming.all_text_contents(), ["Consumer（consumer）"])
    why_row = row("Consumer").locator(".why")
    test_case.assertEqual(why_row.evaluate("element => element.tagName"), "P")
    test_case.assertEqual(why_row.locator("strong").text_content(), "为什么：")
    test_case.assertEqual(why_row.locator(".source-label").count(), 1)


def _assert_line_summaries(test_case, page, expected):
    totals = expected["features"][expected["root"]]["lines"]["total"]
    total_lines = sum(totals.values())
    bar_width = page.locator("#summary-bar").evaluate(
        "element => element.getBoundingClientRect().width"
    )
    for key, count in totals.items():
        if count:
            width = page.locator(f"#summary-bar > .{key}").evaluate(
                "element => element.getBoundingClientRect().width"
            )
            test_case.assertLessEqual(
                abs(width - bar_width * count / total_lines), 2, key
            )
    for feature_id, data in expected["features"].items():
        if feature_id == expected["root"] or not any(data["lines"]["total"].values()):
            continue
        widths = (
            _row_for_title(test_case, page, data["title"])
            .locator(".fill i")
            .evaluate_all(
                "parts => parts.map(part => part.getBoundingClientRect().width)"
            )
        )
        test_case.assertTrue(any(width > 0 for width in widths), feature_id)
    test_case.assertEqual(
        _row_for_title(test_case, page, "Future")
        .locator(".feature-total")
        .text_content(),
        "0 行",
    )
    test_case.assertFalse(
        any(
            total.text_content() == "—"
            for total in page.locator(".feature-total").all()
        )
    )
    markers = page.locator("#marker-list").text_content()
    test_case.assertNotIn("还没实现", markers)
    test_case.assertNotIn("没归到功能的代码或测试", markers)
    test_case.assertEqual(page.locator("#decision-filter").text_content(), "等你拍板")
    test_case.assertNotRegex(markers, r"等你拍板\s+\d+")


def _assert_decision_filter(test_case, page, expected):
    button = page.locator("#decision-filter")
    test_case.assertEqual(button.get_attribute("aria-pressed"), "false")
    button.click()
    test_case.assertEqual(button.get_attribute("aria-pressed"), "true")
    visible = set(
        page.locator(
            ".feature-row:not([hidden]) .feature-title .title"
        ).all_text_contents()
    )
    features = expected["features"]

    def has_decision(feature_id):
        data = features[feature_id]
        return "等你拍板" in data["markers"] or any(
            has_decision(child_id) for child_id in data["children"]
        )

    expected_visible = {
        data["title"]
        for feature_id, data in features.items()
        if feature_id != expected["root"] and has_decision(feature_id)
    }
    test_case.assertEqual(visible, expected_visible)
    test_case.assertGreater(page.locator(".own-summary:visible").count(), 0)
    group_section = _row_for_title(test_case, page, "Group").locator(
        "xpath=ancestor::section[1]"
    )
    test_case.assertTrue(group_section.locator(".own-summary").is_visible())
    button.click()
    test_case.assertEqual(button.get_attribute("aria-pressed"), "false")


def _assert_details(test_case, page, root_feature):
    page.locator("#page-title").click()
    panel = page.locator(".detail-panel")
    test_case.assertTrue(panel.is_visible())
    test_case.assertEqual(
        panel.locator("h2").text_content(),
        f"{root_feature['title']}（{root_feature['directory_name']}）",
    )
    test_case.assertIn("上级路径：无", panel.inner_text())
    close_button = panel.get_by_role("button", name="关闭详情")
    test_case.assertEqual(close_button.get_attribute("aria-label"), "关闭详情")
    close_button.click()
    test_case.assertFalse(panel.is_visible())

    _row_for_title(test_case, page, "Guide").locator(".feature-title").click()
    test_case.assertNotIn("出处", panel.locator("h3").all_text_contents())
    test_case.assertEqual(
        panel.locator(".why-detail-body .source-label").text_content(), "你的原话"
    )
    citation = panel.locator("blockquote")
    test_case.assertIn("用户说过的话。", citation.inner_text())
    test_case.assertIn("测试契约", citation.inner_text())
    external = panel.locator(".detail-list").first.locator(".external-use").first
    test_case.assertEqual(external.text_content(), "Claude Code外部")
    test_case.assertEqual(external.evaluate("element => element.tagName"), "SPAN")
    test_case.assertFalse(
        external.evaluate("element => Boolean(element.closest('button'))")
    )

    _row_for_title(test_case, page, "Consumer").locator(".feature-title").click()
    test_case.assertNotIn("出处", panel.locator("h3").all_text_contents())
    test_case.assertEqual(
        panel.locator(".why-detail-body .source-label").text_content(), "你确认过"
    )
    test_case.assertIn("本次回答", panel.inner_text())
    test_case.assertEqual(panel.locator("blockquote").count(), 0)

    future = _row_for_title(test_case, page, "Future")
    future.locator(".feature-title").click()
    test_case.assertNotIn("出处", panel.locator("h3").all_text_contents())
    test_case.assertEqual(
        panel.locator(".why-detail-body .source-label").text_content(),
        "AI 起草 · 等你拍板",
    )
    test_case.assertNotIn("来源：", panel.inner_text())
    future.locator(".feature-title").click()
    items = page.locator(".detail-panel .items-list")
    test_case.assertEqual(
        items.locator(".item-text").all_inner_texts(),
        ["待确认的条目。", "已确认的条目。"],
    )
    test_case.assertEqual(
        items.locator(".source-label.draft").all_inner_texts(), ["等你拍板"]
    )


def _assert_phone_layout(test_case, page):
    test_case.assertNotEqual(
        page.locator("#picture-note").evaluate(
            "element => getComputedStyle(element).whiteSpace"
        ),
        "nowrap",
    )
    panel_limit = page.locator(".detail-panel").evaluate(
        "element => parseFloat(getComputedStyle(element).maxHeight)"
    )
    test_case.assertLessEqual(panel_limit, 0.45 * 844)
    test_case.assertLessEqual(
        page.evaluate("document.documentElement.scrollWidth"),
        page.evaluate("window.innerWidth"),
    )
    test_case.assertLessEqual(
        page.locator(".sticky").evaluate(
            "element => element.getBoundingClientRect().height"
        ),
        50,
    )
    for view, controls in (("图", "#picture-tools"), ("文字", "#text-tools")):
        page.get_by_role("button", name=view, exact=True).click()
        left = page.locator(".view-switch").evaluate(
            "element => element.getBoundingClientRect().right"
        )
        toolbar = page.locator(controls).evaluate(
            "element => { const box = element.getBoundingClientRect(); return {left: box.left, right: box.right}; }"
        )
        test_case.assertGreaterEqual(toolbar["left"], left)
        test_case.assertLessEqual(toolbar["right"], page.evaluate("window.innerWidth"))


class BrowserTreeTest(unittest.TestCase):
    def test_root_branches_follow_greedy_height_order(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(
            executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable"
        )
        output, expected = _make_greedy_layout_rendered_tree(self)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=executable, headless=True
            )
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto((output / "index.html").as_uri())
            positions = page.locator(".picture-node").evaluate_all(
                "nodes => nodes.map(node => ({id: node.dataset.featureId, "
                "x: node.transform.baseVal.consolidate().matrix.e}))"
            )
            x_by_id = {node["id"]: node["x"] for node in positions}
            root_x = x_by_id[expected["root"]]
            root_children = expected["features"][expected["root"]]["children"]
            sides = [x_by_id[feature_id] < root_x for feature_id in root_children]
            self.assertEqual(sides, [True, False, True, False, True, False])

            groups_on_one_side = True
            for branch_id in root_children:
                stack = [branch_id]
                group_sides = set()
                while stack:
                    feature_id = stack.pop()
                    group_sides.add(x_by_id[feature_id] < root_x)
                    stack.extend(expected["features"][feature_id]["children"])
                groups_on_one_side &= len(group_sides) == 1
            self.assertTrue(groups_on_one_side)
            browser.close()

    def test_tree_behaviors_on_one_browser_page(self):
        executable = os.environ.get("XY_REPO_MAP_CHROMIUM")
        self.assertTrue(
            executable, "XY_REPO_MAP_CHROMIUM must name the Chromium executable"
        )
        output, expected, payload, _external_name = _make_rendered_tree(self)
        root = expected["features"][expected["root"]]
        script_errors = []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=executable, headless=True
            )
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.on("pageerror", lambda error: script_errors.append(str(error)))
            page.goto((output / "index.html").as_uri())
            checks = (
                (
                    "默认打开图视图",
                    lambda: _assert_default_picture_view(self, page),
                ),
                (
                    "图视图说明完整且可换行",
                    lambda: _assert_picture_note(self, page),
                ),
                (
                    "只画上下级，不画功能引用",
                    lambda: _assert_picture_edges_are_parent_child_only(
                        self, page, expected
                    ),
                ),
                (
                    "标签和圆互不重叠",
                    lambda: _assert_picture_labels_and_circles_do_not_overlap(
                        self, page
                    ),
                ),
                (
                    "点击节点弹详情且选中描边随开关变化",
                    lambda: _assert_picture_node_opens_details_and_tracks_selection(
                        self, page
                    ),
                ),
                (
                    "键盘 Enter 和空格能打开详情",
                    lambda: _assert_picture_nodes_support_keyboard(self, page),
                ),
                (
                    "详情关联功能会选中图节点",
                    lambda: _assert_related_feature_selects_picture_node(
                        self, page
                    ),
                ),
                (
                    "图文视图切换",
                    lambda: _assert_view_switching(self, page),
                ),
                (
                    "picture zoom and decision filter",
                    lambda: _assert_picture_zoom_and_decision_filter(self, page),
                ),
                (
                    "tree text, expansion, and theme",
                    lambda: _assert_theme_and_tree_rows(self, page, expected, payload),
                ),
                ("root rationale", lambda: _assert_root_rationale(self, page, root)),
                (
                    "feature usage links",
                    lambda: _assert_usage_relations(self, page, expected),
                ),
                ("line totals", lambda: _assert_line_summaries(self, page, expected)),
                (
                    "decision filter and own summary",
                    lambda: _assert_decision_filter(self, page, expected),
                ),
                ("details and draft items", lambda: _assert_details(self, page, root)),
                ("phone viewport", lambda: _assert_phone_layout(self, page)),
            )
            for label, check in checks:
                with self.subTest(page_part=label):
                    check()
            self.assertEqual(script_errors, [])
            browser.close()
