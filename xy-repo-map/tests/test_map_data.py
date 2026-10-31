"""Repository-tree composition and browser projection tests."""
from pathlib import Path
from itertools import permutations
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


def relation_order_objective(order, edges):
    positions = {node: index for index, node in enumerate(order)}
    crossings = 0
    span = sum(abs(positions[source] - positions[target]) for source, target in edges)
    for index, (source, target) in enumerate(edges):
        first, second = sorted((positions[source], positions[target]))
        for other_source, other_target in edges[index + 1:]:
            if len({source, target, other_source, other_target}) < 4:
                continue
            third, fourth = sorted((positions[other_source], positions[other_target]))
            crossings += int(first < third < second < fourth or
                             third < first < fourth < second)
    return crossings, span


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

    def build_sibling_order_fixture(self, sibling_count, edge_indexes):
        sibling_ids = [f"workspace.sibling_{index:02}" for index in range(sibling_count)]
        elements = {"workspace": element("workspace", "工作区", "node", [])}
        elements.update({node_id: element(node_id, node_id, "node", [])
                         for node_id in sibling_ids})
        relations = {
            f"relation-{index}": {
                "source": sibling_ids[source], "target": sibling_ids[target], "title": "",
            }
            for index, (source, target) in enumerate(edge_indexes)
        }
        repository = {
            "path": self.parent, "root_id": "workspace",
            "model": {"elements": elements, "relations": relations}, "owners": {},
        }
        return sibling_ids, build_page_data([repository], "草稿")

    def build_unit_names(self, name, titles, file_owners, unowned_directories):
        repository_root = Path(self.temporary.name) / name
        repository_root.mkdir()
        for directory in unowned_directories:
            (repository_root / directory).mkdir(parents=True)
        owners = {}
        for file_path, owner in file_owners.items():
            path = repository_root / file_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("归属文件。\n", encoding="utf-8")
            owners[file_path] = owner
        elements = {
            element_id: element(element_id, title, "node", [])
            for element_id, title in titles.items()
        }
        repository = {
            "path": repository_root,
            "root_id": "workspace",
            "model": {"elements": elements, "relations": {}},
            "owners": owners,
        }
        return build_page_data([repository], "草稿")[1]["unit_names"]

    def test_subrepository_root_is_attached_to_the_parent_placeholder(self):
        map_data, page_data = self.build("草稿")
        nodes = map_data["nodes"]
        self.assertEqual(set(map_data), {"mode", "nodes"})
        self.assertEqual(nodes["workspace"]["parent"], None)
        self.assertEqual(nodes["workspace.engine"]["parent"], "workspace")
        self.assertEqual(nodes["engine"]["parent"], "workspace.engine")
        self.assertEqual(nodes["engine.cache"]["parent"], "engine")
        self.assertEqual(page_data["visual_children"]["workspace.engine"], ["engine.cache"])
        self.assertEqual(set(nodes), {"workspace", "workspace.flow", "workspace.flow.parse",
                                      "workspace.engine", "engine", "engine.cache"})
        self.assertIn("engine", page_data["nodes"])

    def test_subrepository_names_are_display_metadata_without_changing_map_titles(self):
        map_data, page_data = self.build("草稿")
        self.assertEqual(page_data["unit_names"]["workspace.engine"], ["vendor"])
        self.assertEqual(page_data["unit_names"]["engine"], [])
        self.assertNotIn("repo_names", page_data)
        self.assertEqual(map_data["nodes"]["workspace.engine"]["title"], "引擎仓")
        self.assertEqual(page_data["nodes"]["workspace.engine"]["title"], "引擎仓")

    def test_exclusive_directory_is_the_unit_name(self):
        unit_names = self.build_unit_names(
            "exclusive-directory",
            {"workspace": "工作区", "workspace.feature": "功能"},
            {"feature/main.py": Owner("workspace.feature", "code"),
             "feature/guide.md": Owner("workspace.feature", "docs"),
             "feature/test_feature.py": Owner("workspace.feature", "tests")},
            unowned_directories=(),
        )

        self.assertEqual(unit_names, {"workspace": [], "workspace.feature": ["feature"]})

    def test_parent_and_child_nodes_get_their_shallowest_owned_directories(self):
        unit_names = self.build_unit_names(
            "parent-child-directories",
            {"workspace": "工作区", "workspace.feature": "功能",
             "workspace.feature.child": "子功能"},
            {"src/main.md": Owner("workspace.feature", "docs"),
             "src/child/guide.md": Owner("workspace.feature.child", "docs")},
            unowned_directories=(),
        )

        self.assertEqual(unit_names["workspace.feature"], ["src"])
        self.assertEqual(unit_names["workspace.feature.child"], ["child"])

    def test_multiple_top_level_directories_are_sorted_by_path(self):
        unit_names = self.build_unit_names(
            "multiple-directories",
            {"workspace": "工作区", "workspace.feature": "功能"},
            {"zeta/second.md": Owner("workspace.feature", "docs"),
             "alpha/first.md": Owner("workspace.feature", "docs")},
            unowned_directories=(),
        )

        self.assertEqual(unit_names["workspace.feature"], ["alpha", "zeta"])

    def test_group_only_gets_a_name_when_children_share_a_directory(self):
        separate_directories = self.build_unit_names(
            "separate-group-directories",
            {"workspace": "工作区", "workspace.quality": "质量约束",
             "workspace.quality.rules": "仓库规范", "workspace.quality.iron": "铁律"},
            {"xy-repo-rules/rules.md": Owner("workspace.quality.rules", "docs"),
             "xy-iron-law/iron.md": Owner("workspace.quality.iron", "docs")},
            unowned_directories=(),
        )
        shared_directory = self.build_unit_names(
            "shared-group-directory",
            {"workspace": "工作区", "workspace.quality": "质量约束",
             "workspace.quality.rules": "仓库规范", "workspace.quality.iron": "铁律"},
            {"quality/rules.md": Owner("workspace.quality.rules", "docs"),
             "quality/iron.md": Owner("workspace.quality.iron", "docs")},
            unowned_directories=(),
        )

        self.assertEqual(separate_directories["workspace.quality"], [])
        self.assertEqual(separate_directories["workspace.quality.rules"],
                         ["xy-repo-rules"])
        self.assertEqual(separate_directories["workspace.quality.iron"], ["xy-iron-law"])
        self.assertEqual(shared_directory["workspace.quality"], ["quality"])
        self.assertEqual(shared_directory["workspace.quality.rules"], [])
        self.assertEqual(shared_directory["workspace.quality.iron"], [])

    def test_repository_root_files_do_not_create_a_unit_name(self):
        unit_names = self.build_unit_names(
            "root-file",
            {"workspace": "工作区"},
            {"README.md": Owner("workspace", "docs")},
            unowned_directories=(),
        )

        self.assertEqual(unit_names["workspace"], [])

    def test_directory_shared_by_distinct_top_level_features_belongs_to_root(self):
        unit_names = self.build_unit_names(
            "shared-tests",
            {"workspace": "工作区", "workspace.alpha": "功能甲",
             "workspace.beta": "功能乙"},
            {"tests/shared/test_alpha.md": Owner("workspace.alpha", "tests"),
             "tests/shared/test_beta.md": Owner("workspace.beta", "tests")},
            unowned_directories=(),
        )

        self.assertEqual(unit_names["workspace"], [])
        self.assertEqual(unit_names["workspace.alpha"], [])
        self.assertEqual(unit_names["workspace.beta"], [])

    def test_directory_without_owned_files_does_not_create_a_unit_name(self):
        unit_names = self.build_unit_names(
            "unowned-directory",
            {"workspace": "工作区", "workspace.feature": "功能"},
            {},
            unowned_directories=("repo-map",),
        )

        self.assertEqual(unit_names, {"workspace": [], "workspace.feature": []})

    def test_data_only_directory_does_not_create_a_unit_name(self):
        unit_names = self.build_unit_names(
            "data-only-directory",
            {"workspace": "工作区", "workspace.feature": "功能"},
            {"file-length-exceptions/reason.md": Owner("workspace.feature", "data")},
            unowned_directories=(),
        )

        self.assertEqual(unit_names, {"workspace": [], "workspace.feature": []})

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

    def test_page_data_precomputes_the_exhaustive_sibling_order(self):
        sibling_ids, (map_data, page_data) = self.build_sibling_order_fixture(
            4, [(0, 2), (1, 3)])
        expected_order = [sibling_ids[index] for index in (0, 2, 1, 3)]
        edges = [(sibling_ids[source], sibling_ids[target]) for source, target in
                 [(0, 2), (1, 3)]]
        actual_order = page_data["visual_children"]["workspace"]

        self.assertEqual(actual_order, expected_order)
        self.assertEqual(relation_order_objective(actual_order, edges),
                         min(relation_order_objective(order, edges)
                             for order in permutations(sibling_ids)))
        self.assertEqual(set(map_data), {"mode", "nodes"})

    def test_page_data_uses_the_heuristic_for_more_than_eight_siblings(self):
        sibling_ids, (_, page_data) = self.build_sibling_order_fixture(
            9, [(0, 2), (1, 3)])
        actual_order = page_data["visual_children"]["workspace"]
        edges = [(sibling_ids[source], sibling_ids[target]) for source, target in
                 [(0, 2), (1, 3)]]

        self.assertEqual(set(actual_order), set(sibling_ids))
        self.assertLessEqual(relation_order_objective(actual_order, edges),
                             relation_order_objective(sibling_ids, edges))

if __name__ == "__main__":
    unittest.main()
