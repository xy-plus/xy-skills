"""Generated view behavior without invoking the LikeC4 executable."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.metrics import LineCounts
from repo_map_lib.rendering import generate_views, view_id


def element(element_id, title, kind):
    return {"id": element_id, "kind": kind, "title": title,
            "description": {"txt": f"{title} 功能描述。"}}


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.model = {"elements": {
            "workspace": element("workspace", "工作区", "node"),
            "workspace.flow": element("workspace.flow", "数据流程", "node"),
            "workspace.engine": element("workspace.engine", "引擎子仓", "repo"),
            "engine": element("engine", "引擎", "node"),
            "engine.cache": element("engine.cache", "缓存", "node"),
        }, "relations": {}}
        self.counts = {
            "workspace": LineCounts(10, 2, 1, 3),
            "workspace.flow": LineCounts(4, 1, 0, 1),
            "workspace.engine": LineCounts(8, 2, 0, 4),
            "engine": LineCounts(8, 2, 0, 4),
            "engine.cache": LineCounts(8, 2, 0, 4),
        }

    def test_draft_titles_mark_every_view_and_only_root_is_in_index(self):
        source = generate_views(self.model, ["workspace"], "草稿", self.counts,
                                {"workspace.engine": "engine"}, {"workspace": "Python 1/1"})
        self.assertIn("title '草稿·未经同意 仓库总览'", source)
        self.assertIn("title '草稿·未经同意 工作区'", source)
        titles = [line.strip() for line in source.splitlines() if line.strip().startswith("title ")]
        self.assertGreaterEqual(len(titles), 3)
        self.assertTrue(all("title '草稿·未经同意 " in title for title in titles), titles)
        index = source.split("view index", 1)[1].split("  }", 1)[0]
        self.assertIn("include workspace", index)
        self.assertNotIn("include engine", index)
        self.assertNotIn("include workspace.flow", index)

    def test_repo_placeholder_navigates_to_child_root_view_and_shows_four_counts(self):
        source = generate_views(self.model, ["workspace"], "正式", self.counts,
                                {"workspace.engine": "engine"}, {})
        self.assertIn(f"navigateTo {view_id('engine')}", source)
        self.assertIn("technology '代码 8 · 文档 2 · 注释 0 · 测试 4'", source)
        self.assertIn("view " + view_id("workspace") + " of workspace", source)

    def test_coverage_is_appended_to_element_descriptions(self):
        source = generate_views(self.model, ["workspace"], "正式", self.counts,
                                {"workspace.engine": "engine"}, {"workspace": "Python 2/3"})
        self.assertIn("文档覆盖率：Python 2/3", source)

    def test_formal_view_titles_have_no_draft_marker(self):
        source = generate_views(self.model, ["workspace"], "正式", self.counts,
                                {"workspace.engine": "engine"}, {})
        self.assertNotIn("草稿·未经同意", source)
        self.assertIn("title '仓库总览'", source)


if __name__ == "__main__":
    unittest.main()
