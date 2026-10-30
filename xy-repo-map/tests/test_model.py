"""LikeC4 export constraints and file ownership tests."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.model import model_problems
from repo_map_lib.ownership import check_ownership
from repo_map_lib.model import source_file_problems
from repo_map_lib.constants import STANDARD_KINDS


def element(element_id, kind, links, **extra):
    value = {"id": element_id, "kind": kind, "title": element_id,
             "description": {"txt": "一句清楚的功能描述"}, "style": {}}
    if links is not None:
        value["links"] = links
    value.update(extra)
    return value


def link(url, category):
    return {"url": url, "title": category}


def model(elements, relations):
    return {"elements": elements, "relations": relations}


class ModelTest(unittest.TestCase):
    def test_model_requires_one_node_root_and_descriptions(self):
        cases = [
            (model({"one": element("one", "repo", [])}, {}), "顶层必须恰好有一个 node"),
            (model({"one": {"id": "one", "kind": "node", "title": "One"}}, {}), "description"),
            (model({"one": element("one", "node", []), "two": element("two", "node", [])}, {}),
             "顶层必须恰好有一个 node"),
        ]
        for candidate, message in cases:
            with self.subTest(message=message):
                self.assertTrue(any(message in problem for problem in model_problems(candidate)))

    def test_model_rejects_unknown_element_relationship_and_kind_properties(self):
        candidate = model({"root": element("root", "node", [], technology="Python", tags=["x"]),
                           "root.child": element("root.child", "service", [])}, {
            "r": {"id": "r", "source": {"model": "root"},
                  "target": {"model": "root.child"}, "title": "uses", "metadata": {}}
        })
        problems = model_problems(candidate)
        self.assertTrue(any("technology" in problem for problem in problems), problems)
        self.assertTrue(any("tags" in problem for problem in problems), problems)
        self.assertTrue(any("service" in problem for problem in problems), problems)
        self.assertTrue(any("metadata" in problem for problem in problems), problems)

    def test_repo_placeholders_have_one_repo_link_and_no_children(self):
        candidate = model({
            "root": element("root", "node", []),
            "root.child": element("root.child", "repo", [link("../child", "repo")]),
            "root.child.inner": element("root.child.inner", "node", []),
        }, {})
        problems = model_problems(candidate)
        self.assertTrue(any("repo 元素不能有子元素" in problem for problem in problems), problems)

        missing = model({"root": element("root", "node", []),
                         "root.child": element("root.child", "repo", [])}, {})
        self.assertTrue(any("恰好一个 repo 链接" in problem for problem in model_problems(missing)))

    def test_specification_block_is_allowed_only_in_the_exact_standard_kinds_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo_map = Path(temporary)
            (repo_map / "kinds.c4").write_text(STANDARD_KINDS, encoding="utf-8")
            (repo_map / "model.c4").write_text("model {}\n", encoding="utf-8")
            self.assertEqual(source_file_problems(repo_map), [])

            (repo_map / "extra.c4").write_text("specification { element node }\n", encoding="utf-8")
            self.assertTrue(any("specification" in problem
                                for problem in source_file_problems(repo_map)))

            (repo_map / "extra.c4").unlink()
            (repo_map / "kinds.c4").write_text("specification {}\n", encoding="utf-8")
            self.assertTrue(any("标准版本" in problem
                                for problem in source_file_problems(repo_map)))


class OwnershipTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "repo-map").mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("pass\n", encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_main.py").write_text("pass\n", encoding="utf-8")

    def test_longest_link_path_assigns_each_tracked_file_once(self):
        candidate = model({
            "root": element("root", "node", []),
            "root.source": element("root.source", "node", [link("../src/", "code")]),
            "root.main": element("root.main", "node", [link("../src/main.py", "code")]),
            "root.tests": element("root.tests", "node", [link("../tests/", "tests")]),
        }, {})
        problems, owners = check_ownership(
            self.root, candidate, ["src/main.py", "tests/test_main.py"], [], [])
        self.assertEqual(problems, [])
        self.assertEqual({path: (owner.element, owner.category)
                          for path, owner in owners.items()},
                         {"src/main.py": ("root.main", "code"),
                          "tests/test_main.py": ("root.tests", "tests")})

    def test_owner_keeps_the_element_and_link_category_together(self):
        candidate = model({
            "root": element("root", "node", []),
            "root.source": element("root.source", "node", [link("../src/", "code")]),
        }, {})

        problems, owners = check_ownership(self.root, candidate, ["src/main.py"], [], [])

        self.assertEqual(problems, [])
        self.assertEqual(owners["src/main.py"].element, "root.source")
        self.assertEqual(owners["src/main.py"].category, "code")

    def test_duplicate_links_missing_files_and_unowned_files_are_reported(self):
        candidate = model({
            "root": element("root", "node", []),
            "root.one": element("root.one", "node", [link("../src/main.py", "code")]),
            "root.two": element("root.two", "node", [link("../src/main.py", "docs")]),
        }, {})
        problems, _ = check_ownership(
            self.root, candidate, ["src/main.py", "tests/test_main.py"], [], [])
        self.assertTrue(any("重复链接" in problem for problem in problems), problems)
        self.assertTrue(any("未归属" in problem for problem in problems), problems)

        missing = model({"root": element("root", "node", []), "root.missing": element(
            "root.missing", "node", [link("../not-here.py", "code")])}, {})
        problems, _ = check_ownership(self.root, missing, [], [], [])
        self.assertTrue(any("不存在" in problem for problem in problems), problems)

    def test_gitlink_is_owned_only_by_a_repo_link_to_a_discovered_child(self):
        child = self.root / "vendor"
        child.mkdir()
        (child / ".git").mkdir()
        (child / "repo-map").mkdir()
        candidate = model({"root": element("root", "node", []), "root.vendor": element(
            "root.vendor", "repo", [link("../vendor/", "repo")])}, {})
        problems, owners = check_ownership(self.root, candidate, ["vendor"], ["vendor"], ["vendor"])
        self.assertEqual(problems, [])
        self.assertEqual((owners["vendor"].element, owners["vendor"].category),
                         ("root.vendor", "repo"))

        wrong_type = model({"root": element("root", "node", []), "root.vendor": element(
            "root.vendor", "node", [link("../vendor/", "code")])}, {})
        problems, _ = check_ownership(self.root, wrong_type, ["vendor"], ["vendor"], ["vendor"])
        self.assertTrue(any("submodule" in problem or "gitlink" in problem for problem in problems), problems)

    def test_repo_link_requires_a_child_map_and_every_child_has_one_placeholder(self):
        child = self.root / "vendor"
        child.mkdir()
        (child / ".git").mkdir()
        candidate = model({"root": element("root", "node", []), "root.vendor": element(
            "root.vendor", "repo", [link("../vendor/", "repo")])}, {})
        problems, _ = check_ownership(self.root, candidate, [], [], ["vendor"])
        self.assertTrue(any("缺少 repo-map" in problem for problem in problems), problems)

        no_placeholder = model({"root": element("root", "node", [])}, {})
        problems, _ = check_ownership(self.root, no_placeholder, [], [], ["vendor"])
        self.assertTrue(any("恰好一个 repo 元素" in problem for problem in problems), problems)


if __name__ == "__main__":
    unittest.main()
