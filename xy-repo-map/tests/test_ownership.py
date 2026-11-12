"""Test file ownership for tests and whole-feature markers."""
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "xy-repo-map"))

from repo_map_lib.ownership import _test_code_match  # noqa: E402
from map_fixture import build_map, feature, make_repo  # noqa: E402


class OwnershipTest(unittest.TestCase):
    def test_root_level_test_follows_imported_feature_code(self):
        self.assertEqual(
            _test_code_match("tests/test_owned.py", {"pkg/owned.py"}),
            "pkg/owned.py",
        )
        repository = make_repo(self, {
            "README.md": "A package test fixture.\n",
            "pkg/owned.py": "value = 1\n",
            "tests/test_consumer.py": "import pkg.owned\n",
            "tests/test_owned.py": "import pkg.owned\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("package", "pkg", children=[
                feature("owned", None, entries=["pkg/owned.py"]),
            ]),
        ]))

        result = build_map(self, repository)

        owned_files = [
            path
            for paths in result["features"]["owned"]["files"].values()
            for path in paths
        ]
        self.assertIn("tests/test_consumer.py", owned_files)
        self.assertIn("tests/test_owned.py", owned_files)

    def test_plural_rust_test_name_matches_source_but_tests_rs_does_not(self):
        self.assertEqual(
            _test_code_match("pkg/foo_tests.rs", {"src/foo.rs"}),
            "src/foo.rs",
        )
        self.assertIsNone(_test_code_match("tests.rs", {"src/tests.rs"}))

    def test_test_file_without_imports_marks_its_parent_as_whole(self):
        repository = make_repo(self, {
            "README.md": "A package test fixture.\n",
            "pkg/core.py": "value = 1\n",
            "pkg/tests/test_orphan.py": "def test_orphan():\n    return None\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("package", "pkg", children=[
                feature("core", None, entries=["pkg/core.py"]),
            ]),
        ]))

        result = build_map(self, repository)
        package = result["features"]["package"]

        self.assertIn("pkg/tests/test_orphan.py", package["files"]["whole"])
        self.assertIn("没归到功能的代码或测试", package["markers"])
