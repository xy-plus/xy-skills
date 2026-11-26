from pathlib import Path
import sys
import unittest

REPOSITORY_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "tests"))
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "xy-repo-map"))
from map_fixture import build_map, feature, make_repo, root_feature  # noqa: E402
from repo_map_lib.find_dependencies.dependencies import find_cycles  # noqa: E402


class CycleDerivationTest(unittest.TestCase):
    def build_cycle_map(self, children, files, edges):
        root = root_feature(
            "root",
            "",
            children=children,
            source="原话",
            items=[],
            subrepositories=[],
            external_dependencies=[],
            existing_cycle_imports=[
                {"from": source, "to": target} for source, target in edges
            ],
        )
        return build_map(make_repo(self, files, root))

    def test_cycle_is_listed_on_each_involved_feature(self):
        data = self.build_cycle_map(
            [
                feature("alpha", "alpha", children=[], source="起草", items=[]),
                feature("beta", "beta", children=[], source="原话", items=[]),
            ],
            {
                "alpha/module.py": "import beta.module\n",
                "beta/module.py": "import alpha.module\n",
            },
            (("alpha", "beta"), ("beta", "alpha")),
        )

        alpha = data["features"]["alpha"]
        self.assertEqual(alpha["cycle_with"], ["beta"])
        self.assertEqual(alpha["markers"], ["等你拍板", "互相引用"])
        self.assertEqual(data["features"]["beta"]["cycle_with"], ["alpha"])

    def test_cycles_use_code_imports_and_exclude_tests_and_nested_features(self):
        children = [
            feature(name, name, children=[], source="原话", items=[])
            for name in ("alpha", "beta", "gamma", "delta", "epsilon", "single")
        ]
        children.append(
            feature(
                "container",
                "container",
                children=[
                    feature(
                        "nested",
                        "container/nested",
                        children=[],
                        source="原话",
                        items=[],
                    )
                ],
                source="原话",
                items=[],
            )
        )
        edges = (
            ("alpha", "beta"),
            ("alpha", "gamma"),
            ("beta", "gamma"),
            ("gamma", "alpha"),
        )

        data = self.build_cycle_map(
            children,
            {
                "alpha/main.py": "import beta.module\ndef load():\n    import gamma.module\n",
                "beta/module.py": "import gamma.module\n",
                "gamma/module.py": "import alpha.main\n",
                "delta/module.py": "import delta.tests.test_delta\n",
                "delta/tests/test_delta.py": "import epsilon.module\n",
                "epsilon/module.py": "value = 1\n",
                "epsilon/tests/test_epsilon.py": "import delta.module\n",
                "container/main.py": "import container.nested.child\n",
                "container/nested/child.py": "import container.main\n",
                "single/one.py": "import single.two\n",
                "single/two.py": "import single.one\n",
            },
            edges,
        )

        self.assertEqual(data["features"]["alpha"]["cycle_with"], ["beta", "gamma"])
        self.assertEqual(data["features"]["beta"]["cycle_with"], ["alpha", "gamma"])
        self.assertEqual(data["features"]["gamma"]["cycle_with"], ["alpha", "beta"])
        for feature_id in ("delta", "epsilon", "container", "nested", "single"):
            with self.subTest(feature=feature_id):
                self.assertEqual(data["features"][feature_id]["cycle_with"], [])

    def test_components_only_include_multi_feature_cycles(self):
        components, cycle_edges = find_cycles(
            [
                ("a", "b"),
                ("b", "c"),
                ("c", "a"),
                ("c", "d"),
                ("d", "e"),
                ("e", "d"),
                ("z", "z"),
            ]
        )

        self.assertEqual(components, [("a", "b", "c"), ("d", "e")])
        self.assertEqual(
            cycle_edges,
            {
                ("a", "b"),
                ("b", "c"),
                ("c", "a"),
                ("d", "e"),
                ("e", "d"),
            },
        )

    def test_components_accept_a_one_pass_edge_iterator(self):
        self.assertEqual(
            find_cycles(iter([("left", "right"), ("right", "left")])),
            ([ ("left", "right") ], {("left", "right"), ("right", "left")}),
        )
