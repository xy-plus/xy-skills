import sys
from pathlib import Path

REPOSITORY_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from map_fixture import MISSING, empty_root_feature, feature  # noqa: E402
from test_feature_check import FeatureCheckBase  # noqa: E402


def cycle_root(registry=MISSING):
    root = empty_root_feature()
    root["children"] = [
        feature("alpha", "alpha", children=[], source="原话", items=[]),
        feature("beta", "beta", children=[], source="原话", items=[]),
    ]
    if registry is MISSING:
        root.pop("existing_cycle_imports")
    else:
        root["existing_cycle_imports"] = registry
    return root


class CycleFeatureCheckTest(FeatureCheckBase):
    def check_cycle_root(self, root, sources=()):
        source_files = {
            "alpha/module.py": "value = 1\n",
            "beta/module.py": "value = 1\n",
        }
        source_files.update(sources)
        for path, content in source_files.items():
            self.write(path, content)
        self.set_features(root)
        result = self.check()
        return result.returncode, result.stdout + result.stderr

    def test_rejects_stale_and_unknown_existing_cycle_imports(self):
        status, output = self.check_cycle_root(
            cycle_root(
                [
                    {"from": "alpha", "to": "beta"},
                    {"from": "ghost", "to": "beta"},
                ]
            )
        )

        self.assertEqual(status, 1, output)
        self.assertIn("alpha → beta 已不成环", output)
        self.assertIn("ghost → beta 已不成环或功能 id 不在图上", output)

    def test_new_cycle_edge_reports_its_source_line(self):
        status, output = self.check_cycle_root(
            cycle_root([]),
            (
                ("alpha/module.py", "import beta.module\n"),
                ("beta/module.py", "import alpha.module\n"),
            ),
        )

        self.assertEqual(status, 1, output)
        self.assertIn("名单只能删不能加", output)
        self.assertIn("alpha/module.py:1 → beta/module.py", output)

    def test_accepts_an_empty_existing_cycle_registry_on_the_root(self):
        status, output = self.check_cycle_root(cycle_root([]))

        self.assertEqual(status, 0, output)

    def test_rejects_invalid_existing_cycle_import_registry_entries(self):
        cases = (
            ("invalid", "必须是数组"),
            (["alpha->beta"], "必须是对象"),
            ([{"from": "alpha"}], "恰有 from、to"),
            ([{"from": "alpha", "to": "beta", "why": "old"}], "恰有 from、to"),
            ([{"from": "", "to": "beta"}], "from 必须是非空字符串"),
            ([{"from": "alpha", "to": ""}], "to 必须是非空字符串"),
            ([{"from": "alpha", "to": "alpha"}], "不能自己引用"),
            (
                [
                    {"from": "alpha", "to": "beta"},
                    {"from": "alpha", "to": "beta"},
                ],
                "不能重复",
            ),
        )
        for registry, expected in cases:
            with self.subTest(registry=registry):
                status, output = self.check_cycle_root(cycle_root(registry))
                self.assertEqual(status, 1, output)
                self.assertIn(expected, output)

    def test_missing_registry_reports_current_cycle_edges(self):
        status, output = self.check_cycle_root(
            cycle_root(),
            (
                ("alpha/module.py", "import beta.module\n"),
                ("beta/module.py", "import alpha.module\n"),
            ),
        )

        self.assertEqual(status, 1, output)
        self.assertIn("根功能缺 existing_cycle_imports", output)
        self.assertIn(
            '[{"from": "alpha", "to": "beta"}, '
            '{"from": "beta", "to": "alpha"}]',
            output,
        )

    def test_child_cannot_declare_existing_cycle_imports(self):
        root = cycle_root([])
        root["children"][0]["existing_cycle_imports"] = []

        status, output = self.check_cycle_root(root)

        self.assertEqual(status, 1, output)
        self.assertIn("子功能有多余键 existing_cycle_imports", output)
