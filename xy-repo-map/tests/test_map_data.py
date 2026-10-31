"""Repository-tree composition and browser projection tests."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.map_data import build_page_data
from repo_map_lib.ownership import Owner


def element(element_id, title, kind, links):
    return {"id": element_id, "title": title, "kind": kind,
            "description": {"txt": f"{title} 描述。"}, "links": links}


class MapDataTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name) / "workspace"
        self.child = self.parent / "vendor"
        for repository in (self.parent, self.child):
            (repository / "repo-map").mkdir(parents=True)
        (self.parent / "src").mkdir()
        (self.child / "src").mkdir()
        (self.parent / "README.md").write_text("Workspace docs.\n", encoding="utf-8")
        (self.parent / "src" / "main.py").write_text(
            'def main():\n    """Documented entry."""\n    return 1\n', encoding="utf-8")
        (self.child / "README.md").write_text("Engine docs.\n", encoding="utf-8")
        (self.child / "src" / "engine.py").write_text(
            "def run():\n    return 1\n", encoding="utf-8")
        self.parent_model = {
            "elements": {
                "workspace": element("workspace", "工作区", "node", [
                    {"url": "../README.md", "title": "docs"},
                ]),
                "workspace.flow": element("workspace.flow", "数据流", "node", [
                    {"url": "../src/main.py", "title": "code"},
                ]),
                "workspace.flow.parse": element("workspace.flow.parse", "解析", "node", []),
                "workspace.engine": element("workspace.engine", "引擎仓", "repo", [
                    {"url": "../vendor/", "title": "repo"},
                ]),
            },
            "relations": {
                "uses-a": {"source": "workspace.flow.parse", "target": "workspace.engine",
                           "title": "调用"},
                "uses-b": {"source": "workspace.flow.parse", "target": "workspace.engine",
                           "title": "读取"},
            },
        }
        self.child_model = {
            "elements": {
                "engine": element("engine", "引擎", "node", [
                    {"url": "../src/engine.py", "title": "code"},
                ]),
                "engine.cache": element("engine.cache", "缓存", "node", [
                    {"url": "../README.md", "title": "docs"},
                ]),
            },
            "relations": {
                "cache-use": {"source": "engine.cache", "target": "engine", "title": "读取"},
            },
        }
        self.repositories = [
            {
                "path": self.parent, "root_id": "workspace", "model": self.parent_model,
                "owners": {
                    "README.md": Owner("workspace", "docs"),
                    "src/main.py": Owner("workspace.flow", "code"),
                    "vendor": Owner("workspace.engine", "repo"),
                },
            },
            {
                "path": self.child, "root_id": "engine", "model": self.child_model,
                "owners": {
                    "README.md": Owner("engine.cache", "docs"),
                    "src/engine.py": Owner("engine", "code"),
                },
            },
        ]

    def build(self, mode):
        def existing_ownership(repository, *_):
            resolved = Path(repository).resolve()
            item = next(item for item in self.repositories
                        if Path(item["path"]).resolve() == resolved)
            return [], item["owners"]

        with patch("repo_map_lib.map_data.tracked_paths", create=True) as path_scan, \
                patch("repo_map_lib.map_data.gitlink_paths", return_value=set(),
                      create=True) as gitlink_scan, \
                patch("repo_map_lib.map_data.discover_subrepositories",
                      create=True) as subrepo_scan, \
                patch("repo_map_lib.map_data.check_ownership", create=True,
                      side_effect=existing_ownership) as ownership_check:
            result = build_page_data(self.repositories, mode)
        path_scan.assert_not_called()
        gitlink_scan.assert_not_called()
        subrepo_scan.assert_not_called()
        ownership_check.assert_not_called()
        return result

    def test_subrepository_root_is_attached_to_the_parent_placeholder(self):
        map_data, page_data = self.build("草稿")
        nodes = map_data["nodes"]
        self.assertEqual(set(map_data), {"mode", "nodes"})
        self.assertEqual(nodes["workspace"]["parent"], None)
        self.assertEqual(nodes["workspace.engine"]["parent"], "workspace")
        self.assertEqual(nodes["engine"]["parent"], "workspace.engine")
        self.assertEqual(nodes["engine.cache"]["parent"], "engine")
        self.assertEqual(set(nodes), {"workspace", "workspace.flow", "workspace.flow.parse",
                                      "workspace.engine", "engine", "engine.cache"})
        self.assertIn("engine", page_data["nodes"])

    def test_own_total_and_coverage_include_subrepository_contents(self):
        map_data, page_data = self.build("草稿")
        nodes = map_data["nodes"]
        self.assertEqual(nodes["workspace"]["own"]["docs"], 1)
        self.assertGreater(nodes["workspace.flow"]["own"]["code"], 0)
        self.assertGreater(nodes["engine"]["own"]["code"], 0)
        self.assertEqual(nodes["engine.cache"]["own"]["docs"], 1)
        self.assertEqual(nodes["workspace.engine"]["own"],
                         {"code": 0, "docs": 0, "comments": 0, "tests": 0})
        self.assertEqual(nodes["workspace.engine"]["total"], nodes["engine"]["total"])
        self.assertEqual(page_data["nodes"]["workspace"]["coverage"], "Python 1/2")

    def test_drill_promotes_descendant_relations_and_merges_labels(self):
        _, page_data = self.build("草稿")
        view = page_data["drill_views"]["workspace"]
        self.assertEqual(view["nodes"], ["workspace.engine", "workspace.flow"])
        self.assertEqual(view["relations"], [{
            "source": "workspace.flow", "target": "workspace.engine", "title": "调用 · 读取",
        }])

    def test_expanded_relations_are_scoped_to_their_common_visual_container(self):
        _, page_data = self.build("草稿")
        self.assertEqual({relation["container"] for relation in page_data["relations"]},
                         {"workspace"})

if __name__ == "__main__":
    unittest.main()
