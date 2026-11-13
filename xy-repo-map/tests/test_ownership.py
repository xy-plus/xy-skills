from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "xy-repo-map"))

from repo_map_lib.ownership import _test_code_match  # noqa: E402
from map_fixture import build_map, feature, make_repo  # noqa: E402


class OwnershipTest(unittest.TestCase):
    def test_root_level_consumer_follows_imported_feature_code(self):
        repository = make_repo(self, {
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
