"""Real browser checks for expanded tree rendering and phone interaction."""
import re
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

    def test_phone_and_desktop_share_self_repository_layout_and_fit_on_open(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                url = f"{self.base_url}/self-draft/index.html#%E5%B1%95%E5%BC%80"
                desktop = browser.new_page(viewport={"width": 1440, "height": 900})
                phone = browser.new_page(viewport={"width": 390, "height": 844},
                                         device_scale_factor=1, is_mobile=True, has_touch=True)
                errors = []
                for page in (desktop, phone):
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(url, wait_until="load")
                    page.wait_for_function("document.querySelectorAll('.card').length > 0",
                                           timeout=30000)
                self.assertEqual(errors, [])
                self.assertEqual(self.layout_snapshot(phone), self.layout_snapshot(desktop))
                self.assertLessEqual(self.scene_scale(phone.locator("#scene")), 1)
                self.assertGreater(self.scene_scale(phone.locator("#scene")), .08)
                self.assert_scene_fits(desktop)
                self.assert_scene_fits(phone)
                self.assert_relation_geometry(phone)
                for fragment in ("#%E9%92%BB%E5%85%A5/workspace",
                                 "#%E9%92%BB%E5%85%A5/workspace.session"):
                    for page in (desktop, phone):
                        page.goto(f"{self.base_url}/self-draft/index.html{fragment}",
                                  wait_until="load")
                        page.wait_for_function("document.querySelectorAll('.card').length > 0")
                    self.assertEqual(self.layout_snapshot(phone), self.layout_snapshot(desktop),
                                     fragment)
            finally:
                browser.close()

    def test_long_relation_routes_around_intermediate_cards(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(f"{self.base_url}/self-draft/index.html#%E5%B1%95%E5%BC%80",
                          wait_until="load")
                page.wait_for_function("document.querySelectorAll('.card').length > 0")
                geometry = page.evaluate("""() => {
                  const edge=[...document.querySelectorAll('.relation-edge')].find(path =>
                    path.dataset.source==='xy_skills.session_work' &&
                    path.dataset.target==='xy_skills.quality');
                  const allCards=[...document.querySelectorAll('.card')].map(card=>{
                    const box=card.querySelector('.card-shape').getBBox();
                    const matrix=card.transform.baseVal.consolidate().matrix;
                    return {id:card.dataset.nodeId,left:matrix.e,right:matrix.e+box.width,
                      top:matrix.f,bottom:matrix.f+box.height};
                  });
                  const data=JSON.parse(document.getElementById('page-data').textContent);
                  const repoAlias=Object.fromEntries(Object.entries(data.repo_roots)
                    .map(([placeholder,root])=>[root,placeholder]));
                  const visualParent=id=>{const parent=data.nodes[id].parent;
                    return parent===null||parent===undefined?parent:(repoAlias[parent]||parent);};
                  const visualDepth=id=>{let depth=0,parent=visualParent(id);
                    while(parent!==null&&parent!==undefined){depth++;parent=visualParent(parent);}return depth;};
                  const endpoints=new Set([edge.dataset.source,edge.dataset.target]);
                  const cards=allCards.filter(card=>!endpoints.has(card.id));
                  const collisions=new Set(),length=edge.getTotalLength();
                  for(let step=1;step<1000;step++){
                    const point=edge.getPointAtLength(length*step/1000);
                    for(const card of cards)if(point.x>card.left&&point.x<card.right&&
                      point.y>card.top&&point.y<card.bottom)collisions.add(card.id);
                  }
                  const box=edge.getBBox(),sourceDepth=visualDepth(edge.dataset.source);
                  const targetDepth=visualDepth(edge.dataset.target);
                  const nextDepth=Math.min(...allCards.map(card=>visualDepth(card.id))
                    .filter(depth=>depth>sourceDepth));
                  const nextColumnLeft=Math.min(...allCards.filter(card=>
                    visualDepth(card.id)===nextDepth).map(card=>card.left));
                  return {collisions:[...collisions],right:box.x+box.width,nextColumnLeft,
                    sourceDepth,targetDepth};
                }""")
                self.assertEqual(geometry["collisions"], [])
                self.assertEqual(geometry["sourceDepth"], geometry["targetDepth"])
                self.assertLessEqual(geometry["right"], geometry["nextColumnLeft"])
            finally:
                browser.close()

    def test_relation_paths_stay_within_card_bounds_plus_fixed_margin(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(f"{self.base_url}/self-draft/index.html#%E5%B1%95%E5%BC%80",
                          wait_until="load")
                page.wait_for_function("document.querySelectorAll('.card').length > 0")
                outside = page.evaluate("""() => {
                  const boxes=[...document.querySelectorAll('.card')].map(card=>{
                    const shape=card.querySelector('.card-shape').getBBox();
                    const matrix=card.transform.baseVal.consolidate().matrix;
                    return {id:card.dataset.nodeId,left:matrix.e,right:matrix.e+shape.width,
                      top:matrix.f,bottom:matrix.f+shape.height};
                  });
                  const cards={left:Math.min(...boxes.map(box=>box.left)),
                    right:Math.max(...boxes.map(box=>box.right)),
                    top:Math.min(...boxes.map(box=>box.top)),
                    bottom:Math.max(...boxes.map(box=>box.bottom))};
                  const margin=128;
                  const paths=[...document.querySelectorAll('.relation-edge')].map(path=>{
                    const box=path.getBBox();
                    return {source:path.dataset.source,target:path.dataset.target,
                      left:Math.max(0,cards.left-margin-box.x),
                      right:Math.max(0,box.x+box.width-cards.right-margin),
                      top:Math.max(0,cards.top-margin-box.y),
                      bottom:Math.max(0,box.y+box.height-cards.bottom-margin),
                      box:{left:box.x,right:box.x+box.width}};
                  });
                  const outside=paths.filter(box=>box.left||box.right||box.top||box.bottom);
                  const data=JSON.parse(document.getElementById('page-data').textContent);
                  const repoAlias=Object.fromEntries(Object.entries(data.repo_roots)
                    .map(([placeholder,root])=>[root,placeholder]));
                  const visualParent=id=>{const parent=data.nodes[id].parent;
                    return parent===null||parent===undefined?parent:(repoAlias[parent]||parent);};
                  const visualDepth=id=>{let depth=0,parent=visualParent(id);
                    while(parent!==null&&parent!==undefined){depth++;parent=visualParent(parent);}return depth;};
                  const beyondNextColumn=paths.filter(path=>{
                    const sourceDepth=visualDepth(path.source),targetDepth=visualDepth(path.target);
                    if(sourceDepth!==targetDepth)return false;
                    const nextDepth=Math.min(...boxes.map(card=>visualDepth(card.id))
                      .filter(depth=>depth>sourceDepth));
                    const nextLeft=Math.min(...boxes.filter(card=>visualDepth(card.id)===nextDepth)
                      .map(card=>card.left));
                    return Number.isFinite(nextLeft)&&path.box.right>nextLeft+1;
                  }).map(path=>[path.source,path.target,path.box.right]);
                  return {outside:outside.map(({box,...rest})=>rest),beyondNextColumn};
                }""")
                self.assertEqual(outside["outside"], [])
                self.assertEqual(outside["beyondNextColumn"], [])
            finally:
                browser.close()

    def test_expansion_is_a_tree_and_draws_each_relation_separately(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page, _remote_requests = self.open_page(
                    browser, "draft", None, {"width": 1440, "height": 900}, False)
                self.wait_for_view(page, "expand", None)
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
                self.assertLessEqual(self.scene_scale(page.locator("#scene")), 1)
                self.assertLessEqual(page.locator("#bar").evaluate(
                    "bar => bar.scrollWidth - bar.clientWidth"), 1)
                for selector in ("#mode", "#fit", "#relations", "#zoom-in", "#zoom-out"):
                    bounds = page.locator(selector).bounding_box()
                    self.assertGreaterEqual(bounds["width"], 40, selector)
                    self.assertGreaterEqual(bounds["height"], 40, selector)
                self.assertGreater(page.locator("#draft").bounding_box()["width"], 0)

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
                page.locator("#fit").click()

                relation_card = page.locator(
                    '.card[data-node-id="workspace.session.process"]')
                relation_box = relation_card.bounding_box()
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
                self.touch(cdp, "touchStart", [{"id": 6, "x": 385, "y": 839}])
                self.touch(cdp, "touchEnd", [])
                self.assertEqual(page.locator(".relation-edge.highlighted").count(), 0)
                self.assertEqual(page.locator(".relation-label.highlighted").count(), 0)

                tap_card = page.locator('.card[data-node-id="workspace.engine"]')
                tap_box = tap_card.bounding_box()
                tapped = {"x": tap_box["x"] + tap_box["width"] / 2,
                          "y": tap_box["y"] + tap_box["height"] / 2}
                self.touch(cdp, "touchStart", [{"id": 5, **tapped}])
                self.touch(cdp, "touchEnd", [])
                self.wait_for_view(page, "drill", "workspace.engine")

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
                page.goto(f"{self.base_url}/draft/index.html#%E9%92%BB%E5%85%A5/workspace.session",
                          wait_until="load")
                self.wait_for_view(page, "drill", "workspace.session")
                session_relations = self.page_data(page)["drill_views"]["workspace.session"]["relations"]
                self.assertEqual(page.locator("#drill-edge-layer .edge").count(),
                                 len(session_relations))
            finally:
                browser.close()

    def touch(self, cdp, event_type, points):
        cdp.send("Input.dispatchTouchEvent", {"type": event_type, "touchPoints": points})

    def scene_scale(self, scene):
        transform = scene.get_attribute("transform")
        match = re.search(r"scale\(([-\d.]+)\)", transform)
        self.assertIsNotNone(match)
        return float(match.group(1))

    def layout_snapshot(self, page):
        return page.evaluate("""() => ({
          cards:[...document.querySelectorAll('.card')].map(card=>[
            card.dataset.nodeId,card.getAttribute('transform')]).sort((a,b)=>a[0].localeCompare(b[0])),
          tree:[...document.querySelectorAll('.composition-spine,.composition-edge')]
            .map(edge=>[edge.dataset.source||'',edge.dataset.target||'',edge.getAttribute('d')])
            .sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b))),
          drill:[...document.querySelectorAll('#drill-edge-layer .edge')].map(edge=>[
            edge.dataset.source||'',edge.dataset.target||'',edge.getAttribute('d')])
            .sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b))),
          relations:[...document.querySelectorAll('.relation-edge')].map(edge=>[
            edge.dataset.source,edge.dataset.target,edge.dataset.title,edge.getAttribute('d')])
            .sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)))
        })""")

    def scene_translation(self, scene):
        transform = scene.get_attribute("transform")
        match = re.search(r"translate\(([-\d.]+) ([-\d.]+)\)", transform)
        self.assertIsNotNone(match)
        return float(match.group(1)), float(match.group(2))

    def assert_scene_fits(self, page):
        bounds = page.evaluate("""() => {
          const view=document.querySelector('#map').getBoundingClientRect();
          const scene=document.querySelector('#scene').getBoundingClientRect();
          return {view:{left:view.left,top:view.top,right:view.right,bottom:view.bottom},
            scene:{left:scene.left,top:scene.top,right:scene.right,bottom:scene.bottom}};
        }""")
        self.assertGreaterEqual(bounds["scene"]["left"], bounds["view"]["left"] + 24, bounds)
        self.assertGreaterEqual(bounds["scene"]["top"], bounds["view"]["top"] + 24, bounds)
        self.assertLessEqual(bounds["scene"]["right"], bounds["view"]["right"] - 24, bounds)
        self.assertLessEqual(bounds["scene"]["bottom"], bounds["view"]["bottom"] - 24, bounds)

    def assert_relation_geometry(self, page):
        geometry = page.evaluate("""() => {
          const boxes=new Map([...document.querySelectorAll('.card')].map(card=>{
            const shape=card.querySelector('.card-shape').getBBox();
            const matrix=card.transform.baseVal.consolidate().matrix;
            return [card.dataset.nodeId,{left:matrix.e,right:matrix.e+shape.width,
              top:matrix.f,bottom:matrix.f+shape.height}];
          }));
          const cards=[...boxes.values()];
          const labels=[...document.querySelectorAll('.relation-label-bg')].map(label=>({
            ...label.getBBox(),source:label.dataset.source,target:label.dataset.target}));
          const edges=[...document.querySelectorAll('.relation-edge')].map(path=>{
            const start=path.getPointAtLength(0),end=path.getPointAtLength(path.getTotalLength());
            let peakX=-Infinity,peakTop=Infinity,peakBottom=-Infinity;
            for(let step=0;step<=500;step++){
              const point=path.getPointAtLength(path.getTotalLength()*step/500);
              if(point.x>peakX+.5){peakX=point.x;peakTop=point.y;peakBottom=point.y;}
              else if(point.x>=peakX-.5){peakTop=Math.min(peakTop,point.y);peakBottom=Math.max(peakBottom,point.y);}
            }
            const label=path.parentElement.querySelector('.relation-label-bg').getBBox();
            return {source:path.dataset.source,target:path.dataset.target,d:path.getAttribute('d'),
              curved:path.getAttribute('d').includes('C'),
              orthogonal:path.getAttribute('d').includes('H')&&path.getAttribute('d').includes('V'),
              startRight:Math.abs(start.x-boxes.get(path.dataset.source).right)<1,
              endRight:Math.abs(end.x-boxes.get(path.dataset.target).right)<1,
              rightBulges:peakX>Math.max(start.x,end.x)+2,
              labelAtPeak:Math.abs(label.x+label.width/2-peakX)<2&&
                label.y+label.height/2>=peakTop-2&&label.y+label.height/2<=peakBottom+2};
          });
          const labelOverlap=labels.some((label,index)=>labels.slice(index+1).some(other=>
            [label.source,label.target].some(id=>id===other.source||id===other.target)&&
            label.x<other.x+other.width&&label.x+label.width>other.x&&
            label.y<other.y+other.height&&label.y+label.height>other.y));
          const cardOverlap=labels.some(label=>cards.some(card=>label.x<card.right&&
            label.x+label.width>card.left&&label.y<card.bottom&&label.y+label.height>card.top));
          const treeLines=[...document.querySelectorAll('.composition-spine,.composition-edge')];
          const treeOverlap=labels.some(label=>treeLines.some(path=>{
            const length=path.getTotalLength(),steps=Math.max(1,Math.ceil(length/2));
            for(let step=0;step<=steps;step++){
              const point=path.getPointAtLength(length*step/steps);
              if(point.x>=label.x-2&&point.x<=label.x+label.width+2&&
                point.y>=label.y-2&&point.y<=label.y+label.height+2)return true;
            }
            return false;
          }));
          return {edges,labelOverlap,cardOverlap,treeOverlap};
        }""")
        self.assertTrue(all(edge["curved"] or edge["orthogonal"] for edge in geometry["edges"]), geometry)
        self.assertTrue(all(edge["startRight"] and edge["endRight"] for edge in geometry["edges"]), geometry)
        self.assertTrue(all(edge["rightBulges"] for edge in geometry["edges"]), geometry)
        self.assertTrue(all(edge["labelAtPeak"] for edge in geometry["edges"]), geometry)
        self.assertFalse(geometry["labelOverlap"], geometry)
        self.assertFalse(geometry["cardOverlap"], geometry)
        self.assertFalse(geometry["treeOverlap"], geometry)


if __name__ == "__main__":
    unittest.main()
