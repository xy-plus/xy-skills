"""Tests for deriving review entities from files owned by each node."""
from pathlib import Path
import sys
import tempfile
import unittest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS.parents[1] / "xy-repo-map"))

from audit_entities import collect_entities
from repo_map_lib.ownership import Owner


class AuditEntitiesTest(unittest.TestCase):
    def test_python_functions_classes_and_methods_have_stable_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "module.py").write_text(
                "def repeated():\n"
                "    return None\n"
                "async def repeated():\n"
                "    return None\n"
                "class Box:\n"
                "    def create(self):\n"
                "        return None\n"
                "    async def create(self):\n"
                "        return None\n",
                encoding="utf-8",
            )
            entities = collect_entities(
                root, {"module.py": Owner("app", "code")}, ["app"]
            )
            self.assertEqual(set(entities["app"]), {
                "module.py",
                "module.py::repeated@1",
                "module.py::repeated@3",
                "module.py::Box",
                "module.py::Box.create@6",
                "module.py::Box.create@8",
            })

    def test_python_test_category_is_parsed_but_other_files_only_list_the_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "tests" / "test_api.py").write_text(
                "def test_api():\n    pass\n", encoding="utf-8"
            )
            (root / "examples.py").write_text(
                "def documented_example():\n    pass\n", encoding="utf-8"
            )
            (root / "README.md").write_text("def not_python(): pass\n", encoding="utf-8")
            entities = collect_entities(root, {
                "tests/test_api.py": Owner("tests", "tests"),
                "examples.py": Owner("docs", "docs"),
                "README.md": Owner("docs", "docs"),
            }, ["tests", "docs"])
            self.assertEqual(entities["tests"], [
                "tests/test_api.py", "tests/test_api.py::test_api"
            ])
            self.assertEqual(entities["docs"], ["README.md", "examples.py"])

    def test_rust_functions_are_found_without_comments_or_string_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "lib.rs").write_text(
                "// fn ignored_line() {}\n"
                "/* fn ignored_block() {} */\n"
                "/// fn ignored_doc() {}\n"
                'const TEXT: &str = "fn ignored_string() {}";\n'
                'const RAW: &str = r#"fn ignored_raw() {}"#;\n'
                "type Callback = fn(i32);\n"
                "fn first() {}\n"
                "fn repeated() {}\n"
                "fn repeated() {}\n"
                "pub async fn fetch() {}\n",
                encoding="utf-8",
            )
            entities = collect_entities(
                root, {"src/lib.rs": Owner("engine", "code")}, ["engine"]
            )
            self.assertEqual(set(entities["engine"]), {
                "src/lib.rs",
                "src/lib.rs::first",
                "src/lib.rs::repeated@8",
                "src/lib.rs::repeated@9",
                "src/lib.rs::fetch",
            })


if __name__ == "__main__":
    unittest.main()
