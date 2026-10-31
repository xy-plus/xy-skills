"""Real browser checks for expanded tree rendering and phone interaction."""
import re
import os
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright

from browser_child_repo_test import ParentChildBrowserTestSupport, run_repo_map


class TreeRenderBrowserAcceptanceTest(ParentChildBrowserTestSupport, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.self_draft_output = cls.site_root / "self-draft"
        self_repo = Path(__file__).resolve().parents[2]
        run_repo_map("render", "--repo", self_repo, "--mode", "草稿",
                     "--out", cls.self_draft_output)

    def test_phone_viewport_renders_self_repository_relations(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844},
                                        device_scale_factor=1, is_mobile=True, has_touch=True)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{self.base_url}/self-draft/index.html#%E5%B1%95%E5%BC%80",
                          wait_until="load")
                page.wait_for_function("document.querySelectorAll('.card').length > 0",
                                       timeout=30000)
                self.assertEqual(errors, [])
                self.assertEqual(self.scene_scale(page.locator("#scene")), 1)
                self.assert_relation_geometry(page)
            finally:
                browser.close()

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
                self.assert_relation_geometry(page)
                label_opacities = page.locator(".relation-label-bg,.relation-label").evaluate_all(
                    "labels => labels.map(label => Number(getComputedStyle(label).opacity))")
                self.assertTrue(all(opacity == 0 for opacity in label_opacities), label_opacities)
                duplicate_pairs = {(edge["source"], edge["target"]) for edge in rendered
                                   if sum(other["source"] == edge["source"] and
                                          other["target"] == edge["target"] for other in rendered) > 1}
                for source, target in duplicate_pairs:
                    paths = [edge["path"] for edge in rendered
                             if edge["source"] == source and edge["target"] == target]
                    self.assertEqual(len(paths), len(set(paths)), (source, target))
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
                labels = page.locator(".relation-label,.relation-label-bg").evaluate_all(
                    "labels => labels.map(label => ({className:label.getAttribute('class'), "
                    "opacity:Number(getComputedStyle(label).opacity)}))")
                self.assertEqual(sum("highlighted" in label["className"] for label in labels), 4)
                self.assertTrue(all(label["opacity"] > .9 for label in labels
                                    if "highlighted" in label["className"]), labels)
                self.assertTrue(all(label["opacity"] < .2 for label in labels
                                    if "dimmed" in label["className"]), labels)

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
                map_document = self.map_data("draft")
                page_data = self.page_data(page)
                visible_ids = set(map_document["nodes"]) - set(page_data["repo_roots"].values())
                depths = self.visual_depths(map_document, page_data, visible_ids)
                bounds = page.locator(".card").evaluate_all("""cards => cards.map(card => {
                  const box=card.getBoundingClientRect(); return {id:card.dataset.nodeId,
                    left:box.left,right:box.right,top:box.top,bottom:box.bottom};
                })""")
                self.assertEqual(self.scene_scale(page.locator("#scene")), 1)
                root = page_data["top_roots"][0]
                self.assertLess(next(box["top"] for box in bounds if box["id"] == root), 100)
                root_left = min(box["left"] for box in bounds if depths[box["id"]] == 0)
                for box in bounds:
                    self.assertAlmostEqual(box["left"], root_left + depths[box["id"]] * 12, delta=1)
                    self.assertLessEqual(box["right"], phone["width"])
                self.assertTrue(all(bounds[index + 1]["top"] >= bounds[index]["bottom"]
                                    for index in range(len(bounds) - 1)))
                self.assert_relation_geometry(page)
                label_opacities = page.locator(".relation-label-bg,.relation-label").evaluate_all(
                    "labels => labels.map(label => Number(getComputedStyle(label).opacity))")
                self.assertTrue(all(opacity == 0 for opacity in label_opacities), label_opacities)
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
                root_box = next(box for box in card_bounds if box["left"] == root_left)
                self.assertGreaterEqual(root_box["right"] - root_box["left"], 200)

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
                relation_box = self.pan_card_into_view(page, cdp, relation_card, phone, 20)
                self.assertGreaterEqual(relation_box["y"], 56)
                self.assertLessEqual(relation_box["y"] + relation_box["height"], phone["height"])
                held = {"x": relation_box["x"] + relation_box["width"] / 2,
                        "y": relation_box["y"] + relation_box["height"] / 2}
                self.touch(cdp, "touchStart", [{"id": 4, **held}])
                page.wait_for_timeout(600)
                highlighted = page.locator(".relation-edge.highlighted").count()
                self.assertEqual(highlighted, 2)
                self.assertEqual(page.locator(".relation-label.highlighted").count(), 2)
                self.assertEqual(page.locator(".relation-label-bg.highlighted").count(), 2)
                self.touch(cdp, "touchEnd", [])
                self.assert_address(page, "展开")

                tap_card = page.locator('.card[data-node-id="workspace.engine"]')
                tap_box = self.pan_card_into_view(page, cdp, tap_card, phone, 40)
                tapped = {"x": tap_box["x"] + tap_box["width"] / 2,
                          "y": tap_box["y"] + tap_box["height"] / 2}
                self.touch(cdp, "touchStart", [{"id": 5, **tapped}])
                self.touch(cdp, "touchEnd", [])
                self.wait_for_view(page, "drill", "workspace.engine")
                drill_boxes = page.locator(".card").evaluate_all("""cards => cards.map(card => {
                  const box=card.getBoundingClientRect(); return {left:box.left,width:box.width};
                })""")
                self.assertLessEqual(max(box["left"] for box in drill_boxes) -
                                     min(box["left"] for box in drill_boxes), 1)
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
                    "cards => cards.map(card => card.getBoundingClientRect().left)")
                self.assertLessEqual(max(root_card_lefts) - min(root_card_lefts), 1)
                page.goto(f"{self.base_url}/draft/index.html#%E9%92%BB%E5%85%A5/workspace.session",
                          wait_until="load")
                self.wait_for_view(page, "drill", "workspace.session")
                session_relations = self.page_data(page)["drill_views"]["workspace.session"]["relations"]
                self.assertEqual(page.locator("#drill-edge-layer .edge").count(),
                                 len(session_relations))
                relation_card_lefts = page.locator(".card").evaluate_all(
                    "cards => cards.map(card => card.getBoundingClientRect().left)")
                self.assertLessEqual(max(relation_card_lefts) - min(relation_card_lefts), 1)
            finally:
                browser.close()

    def touch(self, cdp, event_type, points):
        cdp.send("Input.dispatchTouchEvent", {"type": event_type, "touchPoints": points})

    def pan_card_into_view(self, page, cdp, card, viewport, first_gesture):
        for gesture in range(16):
            bounds = card.bounding_box()
            center_y = bounds["y"] + bounds["height"] / 2
            if 80 <= center_y <= viewport["height"] - 80:
                return bounds
            delta = max(-180, min(180, 420 - center_y))
            start = {"x": 370, "y": 440}
            pointer_id = first_gesture + gesture
            self.touch(cdp, "touchStart", [{"id": pointer_id, **start}])
            self.touch(cdp, "touchMove", [{"id": pointer_id, "x": start["x"],
                                            "y": start["y"] + delta}])
            self.touch(cdp, "touchEnd", [])
        return card.bounding_box()

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

    def visual_depths(self, map_document, page_data, visible_ids):
        aliases = {root: placeholder for placeholder, root in page_data["repo_roots"].items()}

        def parent(element_id):
            value = map_document["nodes"][element_id]["parent"]
            return aliases.get(value, value)

        depths = {}
        for element_id in visible_ids:
            depth, current = 0, element_id
            while parent(current) is not None:
                current = parent(current)
                depth += 1
            depths[element_id] = depth
        return depths

    def assert_relation_geometry(self, page):
        geometry = page.evaluate("""() => {
          const boxes=new Map([...document.querySelectorAll('.card')].map(card=>{
            const shape=card.querySelector('.card-shape').getBBox();
            const matrix=card.transform.baseVal.consolidate().matrix;
            return [card.dataset.nodeId,{left:matrix.e,right:matrix.e+shape.width,
              top:matrix.f,bottom:matrix.f+shape.height}];
          }));
          const cards=[...boxes.values()],treeRight=Math.max(...cards.map(box=>box.right));
          const labels=[...document.querySelectorAll('.relation-label-bg')].map(label=>label.getBBox());
          const edges=[...document.querySelectorAll('.relation-edge')].map(path=>{
            const start=path.getPointAtLength(0),end=path.getPointAtLength(path.getTotalLength());
            let peak=null;
            for(let step=0;step<=500;step++){
              const point=path.getPointAtLength(path.getTotalLength()*step/500);
              if(!peak||point.x>peak.x)peak=point;
            }
            const label=path.parentElement.querySelector('.relation-label-bg').getBBox();
            return {source:path.dataset.source,target:path.dataset.target,d:path.getAttribute('d'),
              startRight:Math.abs(start.x-boxes.get(path.dataset.source).right)<1,
              endRight:Math.abs(end.x-boxes.get(path.dataset.target).right)<1,
              rightBulges:peak.x>Math.max(start.x,end.x)+2,
              labelAtPeak:Math.abs(label.x+label.width/2-peak.x)<2&&
                Math.abs(label.y+label.height/2-peak.y)<2};
          });
          const labelOverlap=labels.some((label,index)=>labels.slice(index+1).some(other=>
            label.x<other.x+other.width&&label.x+label.width>other.x&&
            label.y<other.y+other.height&&label.y+label.height>other.y));
          const cardOverlap=labels.some(label=>cards.some(card=>label.x<card.right&&
            label.x+label.width>card.left&&label.y<card.bottom&&label.y+label.height>card.top));
          const treeLines=[...document.querySelectorAll('.composition-spine,.composition-edge')];
          const lineRight=Math.max(0,...treeLines.flatMap(path=>{
            const length=path.getTotalLength(),points=[];
            for(let step=0;step<=100;step++)points.push(path.getPointAtLength(length*step/100).x);
            return points;
          }));
          const treeOverlap=labels.some(label=>label.x<lineRight+2);
          return {edges,labelOverlap,cardOverlap,treeOverlap};
        }""")
        self.assertTrue(all("C" in edge["d"] for edge in geometry["edges"]), geometry)
        self.assertTrue(all(edge["startRight"] and edge["endRight"] for edge in geometry["edges"]), geometry)
        self.assertTrue(all(edge["rightBulges"] for edge in geometry["edges"]), geometry)
        self.assertTrue(all(edge["labelAtPeak"] for edge in geometry["edges"]), geometry)
        self.assertFalse(geometry["labelOverlap"], geometry)
        self.assertFalse(geometry["cardOverlap"], geometry)
        self.assertFalse(geometry["treeOverlap"], geometry)


if __name__ == "__main__":
    unittest.main()
