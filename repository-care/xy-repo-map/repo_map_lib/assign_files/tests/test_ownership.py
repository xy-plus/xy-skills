import unittest

from pathlib import Path
import sys
REPOSITORY_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists())
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "tests"))
from map_fixture import build_map, feature, make_repo


class OwnershipTest(unittest.TestCase):
    def _repository_with_importing_test(self, consumer_source):
        return make_repo(self, {
            "pkg/owned/main.py": "value = 1\n",
            "pkg/consumer/main.py": consumer_source,
            "pkg/consumer/tests/test_owned.py": "import pkg.owned.main\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("package", "pkg", children=[
                feature("owned", "pkg/owned"),
                feature("consumer", "pkg/consumer"),
            ]),
        ]))

    def test_deepest_feature_directory_owns_files_even_when_other_code_imports_them(self):
        repository = self._repository_with_importing_test("import pkg.owned.main\n")

        result = build_map(self, repository)

        self.assertEqual(result["features"]["owned"]["files"], ["pkg/owned/main.py"])
        self.assertEqual(result["features"]["consumer"]["files"], [
            "pkg/consumer/main.py",
            "pkg/consumer/tests/test_owned.py",
        ])
        self.assertEqual(result["features"]["consumer"]["uses"], [
            {"feature": "owned", "by": "import"},
        ])

    def test_test_file_imports_do_not_create_feature_relationships(self):
        repository = self._repository_with_importing_test("value = 2\n")

        result = build_map(self, repository)

        self.assertEqual(result["features"]["consumer"]["uses"], [])
        self.assertIn("pkg/consumer/tests/test_owned.py", result["features"]["consumer"]["files"])
