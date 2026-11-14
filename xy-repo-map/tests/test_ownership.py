import unittest

from map_fixture import build_map, feature, make_repo


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
