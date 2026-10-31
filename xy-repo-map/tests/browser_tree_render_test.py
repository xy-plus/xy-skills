"""Real browser checks for expanded tree rendering and phone interaction."""
import re
import os
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright

from browser_child_repo_test import ParentChildBrowserTestSupport


class TreeRenderBrowserAcceptanceTest(ParentChildBrowserTestSupport, unittest.TestCase):
    def test_expansion_is_a_tree_and_draws_each_relation_separately(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page, _remote_requests = self.open_page(
                    browser, "draft", None, {"width": 1440, "height": 900}, False)
                self.wait_for_view(page, "expand", None)
                screenshot_directory = os.environ.get("XY_REPO_MAP_SCREENSHOT_DIR")
                if screenshot_directory:
                    page.screenshot(path=str(Path(screenshot_directory) /
                                             "父子仓全部展开-1440x900.png"))
                map_document = self.map_data("draft")
                data = self.page_data(page)
                visible_ids = set(map_document["nodes"]) - set(data["repo_roots"].values())
                self.assert_visible_totals(page, map_document, visible_ids)
                self.assertEqual(page.locator(".container-background").count(), 0)
                self.assertEqual(page.locator(".card").count(), len(visible_ids))
                self.assertTrue(page.locator(".card-shape").evaluate_all(
                    "shapes => shapes.every(shape => getComputedStyle(shape).fill === 'rgb(255, 255, 255)')"))

                aliases = {root: placeholder
                           for placeholder, root in data["repo_roots"].items()}
                expected_tree_edges = set()
                for element_id in visible_ids:
                    parent = map_document["nodes"][element_id]["parent"]
                    parent = aliases.get(parent, parent)
                    if parent is not None:
                        expected_tree_edges.add((parent, element_id))
                tree_edges = page.locator(".composition-edge").evaluate_all(
                    "edges => edges.map(edge => [edge.dataset.source, edge.dataset.target, "
                    "edge.getAttribute('marker-end')])")
                self.assertEqual({(source, target) for source, target, _marker in tree_edges},
                                 expected_tree_edges)
                self.assertEqual(len(tree_edges), len(expected_tree_edges))
                self.assertTrue(all(marker is None for _source, _target, marker in tree_edges))

                relation_rows = data["relations"]
                relation_edges = page.locator(".relation-edge")
                self.assertEqual(relation_edges.count(), len(relation_rows))
                rendered = relation_edges.evaluate_all("""edges => edges.map(edge => ({
                  source:edge.dataset.source, target:edge.dataset.target, title:edge.dataset.title,
                  marker:edge.getAttribute('marker-end'), dash:getComputedStyle(edge).strokeDasharray,
                  path:edge.getAttribute('d')
                }))""")
                self.assertEqual({(edge["source"], edge["target"], edge["title"])
                                  for edge in rendered},
                                 {(edge["source"], edge["target"], edge["title"])
                                  for edge in relation_rows})
                self.assertTrue(all(edge["marker"] and edge["dash"] != "none"
                                    for edge in rendered))
                duplicate_pairs = {(edge["source"], edge["target"]) for edge in rendered
                                   if sum(other["source"] == edge["source"] and
                                          other["target"] == edge["target"] for other in rendered) > 1}
                for source, target in duplicate_pairs:
                    paths = [edge["path"] for edge in rendered
                             if edge["source"] == source and edge["target"] == target]
                    self.assertEqual(len(paths), len(set(paths)), (source, target))
                route_bounds = page.evaluate("""() => {
                  const cards=[...document.querySelectorAll('.card')].map(card=>{
                    const box=card.querySelector('.card-shape').getBBox();
                    const matrix=card.transform.baseVal.consolidate().matrix;
                    return {left:box.x+matrix.e,top:box.y+matrix.f,
                      right:box.x+matrix.e+box.width,bottom:box.y+matrix.f+box.height};
                  });
                  const tree={left:Math.min(...cards.map(box=>box.left)),
                    top:Math.min(...cards.map(box=>box.top)),right:Math.max(...cards.map(box=>box.right)),
                    bottom:Math.max(...cards.map(box=>box.bottom))};
                  const labels=[...document.querySelectorAll('.relation-label-bg')].map(edge=>edge.getBBox());
                  const overlaps=labels.filter(label=>cards.some(card=>label.x<card.right&&
                    label.x+label.width>card.left&&label.y<card.bottom&&label.y+label.height>card.top));
                  const outside=[...document.querySelectorAll('.relation-edge,.relation-label-bg')]
                    .map(edge=>edge.getBBox()).filter(box=>box.x<tree.left-80||box.x+box.width>tree.right+80||
                      box.y<tree.top-80||box.y+box.height>tree.bottom+80);
                  return {tree,outside:outside.length,overlaps:overlaps.length};
                }""")
                self.assertEqual(route_bounds["outside"], 0, route_bounds)
                self.assertEqual(route_bounds["overlaps"], 0, route_bounds)
                self.assertEqual(page.locator(".relation-label").count(), len(relation_rows))
                self.assertEqual(page.locator("#relations").inner_text(), "隐藏关系")
                page.locator("#relations").click()
                self.assertEqual(page.locator("#relations").inner_text(), "显示关系")
                self.assertFalse(page.locator("#relation-layer").is_visible())
                page.locator("#relations").click()
                self.assertTrue(page.locator("#relation-layer").is_visible())

                page.locator('.card[data-node-id="workspace.session.process"]').hover()
                states = page.locator(".relation-edge").evaluate_all("""edges => edges.map(edge => ({
                  source:edge.dataset.source, target:edge.dataset.target,
                  title:edge.dataset.title, className:edge.getAttribute('class')
                }))""")
                related = [edge for edge in states
                           if edge["source"] == "workspace.session.process"]
                unrelated = [edge for edge in states if edge not in related]
                self.assertEqual(len(related), 2)
                self.assertTrue(all("highlighted" in edge["className"] for edge in related))
                self.assertTrue(all("dimmed" in edge["className"] for edge in unrelated))

                self.assertEqual(page.locator('.card[data-node-id="engine"]').count(), 0)
                engine_placeholder = page.locator('.card[data-node-id="workspace.engine"]')
                self.assertEqual(engine_placeholder.locator(".repo-badge-text").text_content(), "子仓")
                self.assertEqual(page.locator(
                    '.composition-edge[data-source="workspace.engine"]').count(), 2)
                page.locator('.card[data-node-id="workspace.session"]').click()
                self.wait_for_view(page, "drill", "workspace.session")
                self.assert_address(page, "钻入/workspace.session")
            finally:
                browser.close()

    def test_phone_viewport_supports_touch_and_keeps_controls_available(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                phone = {"width": 390, "height": 844}
                page, _remote_requests = self.open_page(browser, "draft", None, phone, True)
                self.wait_for_view(page, "expand", None)
                self.assertLessEqual(page.locator("#bar").evaluate(
                    "bar => bar.scrollWidth - bar.clientWidth"), 1)
                for selector in ("#mode", "#fit", "#relations"):
                    bounds = page.locator(selector).bounding_box()
                    self.assertGreaterEqual(bounds["width"], 40, selector)
                    self.assertGreaterEqual(bounds["height"], 40, selector)
                self.assertGreater(page.locator("#draft").bounding_box()["width"], 0)

                card_bounds = page.locator(".card").evaluate_all("""cards => cards.map(card => {
                  const box=card.getBoundingClientRect(); return {left:box.left,right:box.right};
                })""")
                tree_width = max(box["right"] for box in card_bounds) - min(
                    box["left"] for box in card_bounds)
                self.assertGreater(tree_width, 300)
                self.assertLessEqual(tree_width, phone["width"])

                cdp = page.context.new_cdp_session(page)
                scene = page.locator("#scene")
                before_scale = self.scene_scale(scene)
                self.touch(cdp, "touchStart", [
                    {"id": 1, "x": 70, "y": 220}, {"id": 2, "x": 320, "y": 220}])
                self.touch(cdp, "touchMove", [
                    {"id": 1, "x": 60, "y": 220}, {"id": 2, "x": 340, "y": 220}])
                self.touch(cdp, "touchEnd", [])
                self.assertGreater(self.scene_scale(scene), before_scale)
                self.assert_address(page, "展开")

                drag_card = page.locator('.card[data-node-id="workspace.session"]')
                drag_box = drag_card.bounding_box()
                start = {"x": drag_box["x"] + drag_box["width"] / 2,
                         "y": drag_box["y"] + drag_box["height"] / 2}
                before_xy = self.scene_translation(scene)
                self.touch(cdp, "touchStart", [{"id": 3, **start}])
                self.touch(cdp, "touchMove", [{"id": 3, "x": start["x"] + 48,
                                                "y": start["y"] + 27}])
                self.touch(cdp, "touchEnd", [])
                after_xy = self.scene_translation(scene)
                self.assertGreater(abs(after_xy[0] - before_xy[0]) +
                                   abs(after_xy[1] - before_xy[1]), 20)
                self.assert_address(page, "展开")

                relation_card = page.locator(
                    '.card[data-node-id="workspace.session.process"]')
                relation_box = relation_card.bounding_box()
                held = {"x": relation_box["x"] + relation_box["width"] / 2,
                        "y": relation_box["y"] + relation_box["height"] / 2}
                self.touch(cdp, "touchStart", [{"id": 4, **held}])
                page.wait_for_timeout(600)
                highlighted = page.locator(".relation-edge.highlighted").count()
                self.assertEqual(highlighted, 2)
                self.touch(cdp, "touchEnd", [])
                self.assert_address(page, "展开")

                tap_card = page.locator('.card[data-node-id="workspace.engine"]')
                tap_box = tap_card.bounding_box()
                tapped = {"x": tap_box["x"] + tap_box["width"] / 2,
                          "y": tap_box["y"] + tap_box["height"] / 2}
                self.touch(cdp, "touchStart", [{"id": 5, **tapped}])
                self.touch(cdp, "touchEnd", [])
                self.wait_for_view(page, "drill", "workspace.engine")
                drill_boxes = page.locator(".card").evaluate_all("""cards => cards.map(card => {
                  const box=card.getBoundingClientRect(); return {center:box.left+box.width/2,
                    left:box.left,width:box.width};
                })""")
                self.assertLessEqual(max(box["center"] for box in drill_boxes) -
                                     min(box["center"] for box in drill_boxes), 1)
                self.assertTrue(all(box["left"] >= 0 and box["left"] + box["width"] <= 390
                                    for box in drill_boxes))

                page.goto(f"{self.base_url}/draft/index.html#%E9%92%BB%E5%85%A5/"
                          "workspace.session.process.step", wait_until="load")
                self.wait_for_view(page, "drill", "workspace.session.process.step")
                self.assertGreater(page.locator("#crumbs").evaluate(
                    "crumbs => crumbs.scrollWidth"), page.locator("#crumbs").evaluate(
                    "crumbs => crumbs.clientWidth"))
                self.assertLessEqual(page.locator("#bar").evaluate(
                    "bar => bar.scrollWidth - bar.clientWidth"), 1)

                page.goto(f"{self.base_url}/draft/index.html#%E9%92%BB%E5%85%A5/workspace",
                          wait_until="load")
                self.wait_for_view(page, "drill", "workspace")
                root_card_lefts = page.locator(".card").evaluate_all(
                    "cards => cards.map(card => {const box=card.getBoundingClientRect();return box.left+box.width/2;})")
                self.assertLessEqual(max(root_card_lefts) - min(root_card_lefts), 1)
                page.goto(f"{self.base_url}/draft/index.html#%E9%92%BB%E5%85%A5/workspace.session",
                          wait_until="load")
                self.wait_for_view(page, "drill", "workspace.session")
                session_relations = self.page_data(page)["drill_views"]["workspace.session"]["relations"]
                self.assertEqual(page.locator("#drill-edge-layer .edge").count(),
                                 len(session_relations))
                relation_card_centers = page.locator(".card").evaluate_all(
                    "cards => cards.map(card => {const box=card.getBoundingClientRect();return box.left+box.width/2;})")
                self.assertLessEqual(max(relation_card_centers) - min(relation_card_centers), 1)
            finally:
                browser.close()

    def touch(self, cdp, event_type, points):
        cdp.send("Input.dispatchTouchEvent", {"type": event_type, "touchPoints": points})

    def scene_scale(self, scene):
        transform = scene.get_attribute("transform")
        match = re.search(r"scale\(([-\d.]+)\)", transform)
        self.assertIsNotNone(match)
        return float(match.group(1))

    def scene_translation(self, scene):
        transform = scene.get_attribute("transform")
        match = re.search(r"translate\(([-\d.]+) ([-\d.]+)\)", transform)
        self.assertIsNotNone(match)
        return float(match.group(1)), float(match.group(2))


if __name__ == "__main__":
    unittest.main()
