"""Repository-tree composition and browser projection tests."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.map_data import build_page_data, validate_map_data


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
            {"path": self.parent, "root_id": "workspace", "model": self.parent_model},
            {"path": self.child, "root_id": "engine", "model": self.child_model},
        ]

    def build(self, mode):
        tracked_by_path = {
            self.parent.resolve(): ["README.md", "src/main.py"],
            self.child.resolve(): ["README.md", "src/engine.py"],
        }

        def subrepositories(repository):
            return ["vendor"] if Path(repository).resolve() == self.parent.resolve() else []

        with patch("repo_map_lib.map_data.tracked_paths",
                   side_effect=lambda path: tracked_by_path[Path(path).resolve()]), \
                patch("repo_map_lib.map_data.gitlink_paths", return_value=set()), \
                patch("repo_map_lib.map_data.discover_subrepositories",
                      side_effect=subrepositories):
            return build_page_data(self.repositories, mode)

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
        for element_id, item in nodes.items():
            children = [child for child in nodes.values() if child["parent"] == element_id]
            for metric in ("code", "docs", "comments", "tests"):
                self.assertEqual(item["total"][metric],
                                 item["own"][metric] + sum(child["total"][metric]
                                                           for child in children))

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

    def test_drill_breadcrumb_uses_tree_parents_not_click_history(self):
        _, page_data = self.build("草稿")
        self.assertEqual(page_data["drill_views"]["engine.cache"]["breadcrumbs"],
                         ["workspace", "workspace.engine", "engine.cache"])

    def test_populated_repository_placeholder_drills_to_repository_contents(self):
        _, page_data = self.build("草稿")
        self.assertEqual(page_data["drill_views"]["workspace.engine"]["nodes"],
                         ["engine.cache"])

    def test_root_only_repository_placeholder_has_no_next_drill_level(self):
        root = {**self.child_model["elements"]["engine"]}
        root["links"] = [*root["links"], {"url": "../README.md", "title": "docs"}]
        self.child_model["elements"] = {"engine": root}
        self.child_model["relations"] = {}
        _, page_data = self.build("草稿")
        self.assertEqual(page_data["drill_views"]["workspace.engine"]["nodes"],
                         ["workspace.engine"])

    def test_map_data_contract_rejects_invalid_values_and_totals(self):
        valid, _ = self.build("草稿")
        validate_map_data(valid)
        cases = []

        invalid = {**valid, "mode": "preview"}
        cases.append(invalid)
        invalid = {**valid, "extra": True}
        cases.append(invalid)
        invalid = {"mode": valid["mode"], "nodes": {**valid["nodes"],
                  "workspace.flow": {**valid["nodes"]["workspace.flow"], "title": 7}}}
        cases.append(invalid)
        invalid = {"mode": valid["mode"], "nodes": {**valid["nodes"],
                  "workspace.flow": {**valid["nodes"]["workspace.flow"], "kind": "service"}}}
        cases.append(invalid)
        invalid = {"mode": valid["mode"], "nodes": {**valid["nodes"],
                  "workspace.flow": {**valid["nodes"]["workspace.flow"], "parent": "missing"}}}
        cases.append(invalid)
        invalid = {"mode": valid["mode"], "nodes": {**valid["nodes"],
                  "workspace.flow": {**valid["nodes"]["workspace.flow"], "parent": 4}}}
        cases.append(invalid)
        invalid = {"mode": valid["mode"], "nodes": {**valid["nodes"],
                  "workspace.flow": {**valid["nodes"]["workspace.flow"], "extra": "field"}}}
        cases.append(invalid)
        invalid = {"mode": valid["mode"], "nodes": {**valid["nodes"],
                  "workspace.flow": {**valid["nodes"]["workspace.flow"],
                                     "own": {"code": 0, "docs": 0, "comments": 0}}}}
        cases.append(invalid)
        invalid = {"mode": valid["mode"], "nodes": {**valid["nodes"],
                  "workspace.flow": {**valid["nodes"]["workspace.flow"],
                                     "own": {**valid["nodes"]["workspace.flow"]["own"],
                                             "code": True}}}}
        cases.append(invalid)
        invalid = {"mode": valid["mode"], "nodes": {**valid["nodes"],
                  "workspace.flow": {**valid["nodes"]["workspace.flow"],
                                     "own": {**valid["nodes"]["workspace.flow"]["own"],
                                             "docs": -1}}}}
        cases.append(invalid)
        invalid = {"mode": valid["mode"], "nodes": {**valid["nodes"],
                  "workspace.flow": {**valid["nodes"]["workspace.flow"],
                                     "own": {**valid["nodes"]["workspace.flow"]["own"],
                                             "tests": 1.0}}}}
        cases.append(invalid)
        invalid = {"mode": valid["mode"], "nodes": {**valid["nodes"],
                  "workspace.flow": {**valid["nodes"]["workspace.flow"],
                                     "total": {"code": 0, "docs": 0, "comments": 0}}}}
        cases.append(invalid)
        invalid = {"mode": valid["mode"], "nodes": {**valid["nodes"],
                  "workspace.flow": {**valid["nodes"]["workspace.flow"],
                                     "total": {**valid["nodes"]["workspace.flow"]["total"],
                                               "code": 0}}}}
        cases.append(invalid)
        for document in cases:
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    validate_map_data(document)


if __name__ == "__main__":
    unittest.main()
