"""Browser checks for lazy drill layout and precomputed expanded layout."""
import unittest

from playwright.sync_api import sync_playwright

from browser_child_repo_test import ParentChildBrowserTestSupport


class MapLoadSpeedBrowserTest(ParentChildBrowserTestSupport, unittest.TestCase):
    def test_elk_loads_only_on_drill_and_is_reused(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                requests = []
                page.on("request", lambda request: requests.append(request.url)
                        if request.url.endswith("/elk.bundled.js") else None)
                page.goto(f"{self.base_url}/draft/index.html#%E5%B1%95%E5%BC%80",
                          wait_until="load")
                self.wait_for_view(page, "expand", None)
                self.assertEqual(requests, [])

                page.locator('.card[data-node-id="workspace.session"]').click()
                self.wait_for_view(page, "drill", "workspace.session")
                self.assertEqual(len(requests), 1)
                self.assertGreater(page.locator("#drill-edge-layer .edge").count(), 0)

                page.locator("#mode").click()
                self.wait_for_view(page, "expand", None)
                page.locator('.card[data-node-id="workspace.engine"]').click()
                self.wait_for_view(page, "drill", "workspace.engine")
                self.assertEqual(len(requests), 1)

                direct = browser.new_page(viewport={"width": 1440, "height": 900})
                direct_requests = []
                direct.on("request", lambda request: direct_requests.append(request.url)
                          if request.url.endswith("/elk.bundled.js") else None)
                direct.goto(f"{self.base_url}/draft/index.html#%E9%92%BB%E5%85%A5/workspace",
                            wait_until="load")
                self.wait_for_view(direct, "drill", "workspace")
                self.assertEqual(len(direct_requests), 1)
                self.assertGreater(direct.locator(".card").count(), 0)
            finally:
                browser.close()

    def test_expansion_uses_precomputed_order_and_column_layout(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.add_init_script("""(() => {
                  const parse=JSON.parse;
                  JSON.parse=function(text,reviver){
                    const data=parse(text,reviver);
                    if(!window.__forcedOrder&&data.visual_children){
                      data.visual_children['workspace.session'].reverse();
                      window.__forcedOrder=data.visual_children['workspace.session'].slice();
                    }
                    return data;
                  };
                })();""")
                page.goto(f"{self.base_url}/draft/index.html#%E5%B1%95%E5%BC%80",
                          wait_until="load")
                self.wait_for_view(page, "expand", None)
                layout = page.evaluate("""() => {
                  const data=JSON.parse(document.getElementById('page-data').textContent);
                  const aliases=Object.fromEntries(Object.entries(data.repo_roots)
                    .map(([placeholder,root])=>[root,placeholder]));
                  const visualChildren=id=>id==='workspace.session'?window.__forcedOrder:
                    data.visual_children[id];
                  const expected=[];
                  function visit(id){expected.push(id);visualChildren(id).forEach(visit);}
                  data.top_roots.forEach(visit);
                  const cards=[...document.querySelectorAll('.card')].map(card=>{
                    const matrix=card.transform.baseVal.consolidate().matrix;
                    const shape=card.querySelector('.card-shape').getBBox();
                    return {id:card.dataset.nodeId,x:matrix.e,y:matrix.f,w:shape.width,h:shape.height};
                  });
                  function depth(id){let value=0,parent=data.nodes[id].parent;
                    while(parent!==null){value++;parent=aliases[parent]||parent;
                      parent=data.nodes[parent].parent;}return value;}
                  return {expected,actual:cards.map(card=>card.id),cards:cards.map(card=>
                    ({...card,depth:depth(card.id)})),
                    children:{...data.visual_children,'workspace.session':window.__forcedOrder}};
                }""")
                self.assertEqual(layout["actual"], layout["expected"])

                by_depth = {}
                by_id = {card["id"]: card for card in layout["cards"]}
                for card in layout["cards"]:
                    by_depth.setdefault(card["depth"], []).append(card)
                for depth, cards in by_depth.items():
                    self.assertTrue(all(abs(card["x"] - cards[0]["x"]) < 1
                                        for card in cards), (depth, cards))
                    in_tree_order = [node for node in layout["expected"]
                                     if by_id[node]["depth"] == depth]
                    self.assertEqual([card["id"] for card in sorted(
                        cards, key=lambda card: card["y"])], in_tree_order)
                    cards.sort(key=lambda card: card["y"])
                    for first, second in zip(cards, cards[1:]):
                        self.assertGreaterEqual(second["y"] - first["y"] - first["h"], 48 - 1)

                depths = sorted(by_depth)
                for first_depth, second_depth in zip(depths, depths[1:]):
                    first = by_depth[first_depth]
                    second = by_depth[second_depth]
                    right = max(card["x"] + card["w"] for card in first)
                    left = min(card["x"] for card in second)
                    self.assertAlmostEqual(left - right, 120, delta=1)

                for parent, child_ids in layout["children"].items():
                    if not child_ids or parent not in by_id:
                        continue
                    first_child = by_id[child_ids[0]]
                    parent_card = by_id[parent]
                    self.assertAlmostEqual(parent_card["y"] + parent_card["h"] / 2,
                                           first_child["y"] + first_child["h"] / 2,
                                           delta=1, msg=(parent, child_ids[0]))

                for index, first in enumerate(layout["cards"]):
                    for second in layout["cards"][index + 1:]:
                        overlap = (first["x"] < second["x"] + second["w"] and
                                   first["x"] + first["w"] > second["x"] and
                                   first["y"] < second["y"] + second["h"] and
                                   first["y"] + first["h"] > second["y"])
                        self.assertFalse(overlap, (first["id"], second["id"]))
            finally:
                browser.close()

    def test_nested_first_child_collision_moves_highest_aligned_ancestor(self):
        with sync_playwright() as playwright:
            browser = self.open_browser(playwright)
            try:
                page, _ = self.open_page(
                    browser, "draft", "展开", {"width": 1440, "height": 900}, False)
                self.wait_for_view(page, "expand", None)
                layout = page.evaluate("""() => {
                  const data=JSON.parse(document.getElementById('page-data').textContent);
                  const cards=[...document.querySelectorAll('.card')].map(card=>{
                    const matrix=card.transform.baseVal.consolidate().matrix;
                    const shape=card.querySelector('.card-shape').getBBox();
                    return {id:card.dataset.nodeId,x:matrix.e,y:matrix.f,
                      w:shape.width,h:shape.height};
                  });
                  return {cards,children:data.visual_children};
                }""")
                by_id = {card["id"]: card for card in layout["cards"]}
                self.assertIn("workspace.aaa_layout_a.branch.tall", by_id)
                self.assertIn("workspace.bbb_layout_b.child.leaf", by_id)
                self.assertEqual(
                    layout["children"]["workspace"][:2],
                    ["workspace.aaa_layout_a", "workspace.bbb_layout_b"],
                )
                self.assertEqual(
                    layout["children"]["workspace.aaa_layout_a.branch"],
                    ["workspace.aaa_layout_a.branch.first",
                     "workspace.aaa_layout_a.branch.tall"],
                )
                self.assertEqual(
                    layout["children"]["workspace.bbb_layout_b"],
                    ["workspace.bbb_layout_b.child"],
                )
                self.assertEqual(
                    layout["children"]["workspace.bbb_layout_b.child"],
                    ["workspace.bbb_layout_b.child.leaf"],
                )
                self.assertGreater(by_id["workspace.aaa_layout_a.branch.tall"]["h"],
                                   by_id["workspace.bbb_layout_b.child.leaf"]["h"])
                self.assertEqual(by_id["workspace.aaa_layout_a.branch.tall"]["x"],
                                 by_id["workspace.bbb_layout_b.child.leaf"]["x"])

                for parent, child_ids in layout["children"].items():
                    if not child_ids or parent not in by_id:
                        continue
                    parent_card = by_id[parent]
                    first_child = by_id[child_ids[0]]
                    self.assertAlmostEqual(
                        parent_card["y"] + parent_card["h"] / 2,
                        first_child["y"] + first_child["h"] / 2,
                        delta=1,
                        msg=f"{parent} 与首个下级 {child_ids[0]} 未对齐",
                    )

                columns = {}
                for card in layout["cards"]:
                    columns.setdefault(round(card["x"], 2), []).append(card)
                for cards in columns.values():
                    cards.sort(key=lambda card: card["y"])
                    for first, second in zip(cards, cards[1:]):
                        self.assertGreaterEqual(
                            second["y"] - first["y"] - first["h"], 48 - 1,
                            (first["id"], second["id"]),
                        )

                for index, first in enumerate(layout["cards"]):
                    for second in layout["cards"][index + 1:]:
                        overlap = (
                            first["x"] < second["x"] + second["w"]
                            and first["x"] + first["w"] > second["x"]
                            and first["y"] < second["y"] + second["h"]
                            and first["y"] + first["h"] > second["y"]
                        )
                        self.assertFalse(overlap, (first["id"], second["id"]))
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
