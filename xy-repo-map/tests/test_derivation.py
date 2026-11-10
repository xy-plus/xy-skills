"""Test file metrics, dependencies, ownership, and feature markers."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from map_fixture import build_map, feature, make_repo


def child_tree(children, entries=None):
    return feature("root", "", children=children, entries=entries)


class DerivationTest(unittest.TestCase):
    def test_classifies_source_documentation_tests_and_other_files(self):
        repository = make_repo(self, {
            "src/code.py": '"""module docs"""\n# comment\nvalue = 1\n',
            "src/view.html": "<!-- page note -->\n<p>hello</p>\n<div><!-- inline --></div>\n",
            "src/style.css": "/* style note */\nbody { color: red; }\ncolor: blue; /* inline */\n",
            "docs/guide.md": "# Guide\nA page.\n",
            "tests/example.py": '"""test docs"""\n# test comment\n',
            "vendor/lib.py": "value = 2\n",
            "assets/data.json": '{"value": 1}\n',
        }, feature("root", ""))
        data = build_map(self, repository)
        counts = data["features"]["root"]["lines"]["own"]
        self.assertEqual(counts, {"code": 5, "docs": 3, "comments": 3, "tests": 2})

    def test_directory_feature_owns_unclaimed_files(self):
        repository = make_repo(self, {
            "README.md": "root docs\n",
            "pkg/one.py": "one = 1\n",
            "pkg/two.py": "two = 2\n",
        }, child_tree([feature("package", "pkg")]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["package"]["files"]["directory"], ["pkg/one.py", "pkg/two.py"])
        self.assertEqual(
            data["features"]["root"]["files"]["whole"],
            ["README.md", "repo-map/features.json"],
        )

    def test_directory_entry_registers_all_tracked_files_below_it(self):
        package = feature("package", "pkg", children=[
            feature("manuals", None, entries=["pkg/manual/"]),
        ])
        repository = make_repo(self, {
            "pkg/manual/guide.md": "Guide\n",
            "pkg/manual/examples/sample.py": "sample = 1\n",
            "pkg/other.py": "other = 1\n",
        }, child_tree([package]))
        data = build_map(self, repository)
        self.assertEqual(
            data["features"]["manuals"]["files"]["registered"],
            ["pkg/manual/examples/sample.py", "pkg/manual/guide.md"],
        )

    def test_import_reach_stops_at_an_unrelated_sibling_entry(self):
        package = feature("package", "pkg", children=[
            feature("alpha", None, entries=["pkg/main.py"]),
            feature("beta", None, entries=["pkg/stop.py"]),
        ])
        repository = make_repo(self, {
            "pkg/main.py": "from pkg import helper, stop\n",
            "pkg/helper.py": "helper = 1\n",
            "pkg/stop.py": "stop = 1\n",
        }, child_tree([package]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["alpha"]["files"]["registered"], ["pkg/main.py"])
        self.assertEqual(data["features"]["alpha"]["files"]["reached"], ["pkg/helper.py"])
        self.assertEqual(data["features"]["beta"]["files"]["registered"], ["pkg/stop.py"])

    def test_relative_import_reaches_a_sibling_module(self):
        package = feature("package", "pkg", children=[
            feature("alpha", None, entries=["pkg/sub/main.py"]),
        ])
        repository = make_repo(self, {
            "pkg/sub/main.py": "from . import helper\n",
            "pkg/sub/helper.py": "helper = 1\n",
        }, child_tree([package]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["alpha"]["files"]["reached"], ["pkg/sub/helper.py"])

    def test_reached_by_two_sibling_features_is_shared_by_their_parent(self):
        package = feature("package", "pkg", children=[
            feature("alpha", None, entries=["pkg/alpha.py"]),
            feature("beta", None, entries=["pkg/beta.py"]),
        ])
        repository = make_repo(self, {
            "pkg/alpha.py": "from pkg import shared\n",
            "pkg/beta.py": "from pkg import shared\n",
            "pkg/shared.py": "shared = 1\n",
        }, child_tree([package]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["package"]["files"]["shared"], ["pkg/shared.py"])
        self.assertEqual(data["features"]["alpha"]["files"]["shared"], [])

    def test_path_mention_does_not_change_file_ownership(self):
        package = feature("package", "pkg", children=[
            feature("alpha", None, entries=["pkg/readme.md"]),
        ])
        repository = make_repo(self, {
            "pkg/readme.md": "See pkg/orphan.py for details.\n",
            "pkg/orphan.py": "unused = 1\n",
        }, child_tree([package]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["package"]["files"]["whole"], ["pkg/orphan.py"])
        self.assertIn("没归到功能的代码", data["features"]["package"]["markers"])

    def test_unregistered_tests_follow_imported_code_and_share_multiple_targets(self):
        package = feature("package", "pkg", children=[
            feature("alpha", None, entries=["pkg/alpha.py"]),
            feature("beta", None, entries=["pkg/beta.py"]),
        ])
        repository = make_repo(self, {
            "pkg/alpha.py": "alpha = 1\n",
            "pkg/beta.py": "beta = 1\n",
            "pkg/tests/test_alpha.py": "import pkg.alpha\n",
            "pkg/tests/test_multi.py": "import pkg.alpha\nimport pkg.beta\n",
            "pkg/tests/test_beta.py": "import pkg.beta\n",
            "pkg/tests/test_none.py": "pass\n",
        }, child_tree([package]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["alpha"]["files"]["reached"], ["pkg/tests/test_alpha.py"])
        self.assertEqual(data["features"]["beta"]["files"]["reached"], ["pkg/tests/test_beta.py"])
        self.assertEqual(data["features"]["package"]["files"]["shared"], ["pkg/tests/test_multi.py"])
        self.assertEqual(data["features"]["package"]["files"]["whole"], ["pkg/tests/test_none.py"])

    def test_unregistered_test_only_matches_imported_code_in_its_directory_feature(self):
        repository = make_repo(self, {
            "pkg/alpha.py": "alpha = 1\n",
            "pkg/tests/test_alpha.py": "import external.alpha\n",
            "external/alpha.py": "alpha = 2\n",
        }, child_tree([
            feature("package", "pkg", children=[
                feature("alpha", None, entries=["pkg/alpha.py"]),
            ]),
            feature("external", "external"),
        ]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["alpha"]["files"]["reached"], [])
        self.assertIn("pkg/tests/test_alpha.py", data["features"]["package"]["files"]["whole"])

    def test_child_registration_wins_over_ancestor_registration(self):
        package = feature("package", "pkg", children=[
            feature("alpha", None, entries=["pkg/shared.py"]),
        ], entries=["pkg/shared.py"])
        repository = make_repo(self, {"pkg/shared.py": "shared = 1\n"}, child_tree([package]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["alpha"]["files"]["registered"], ["pkg/shared.py"])
        self.assertEqual(data["features"]["package"]["files"]["registered"], [])

    def test_uses_merge_import_and_mention_with_import_taking_precedence(self):
        repository = make_repo(self, {
            "alpha/main.py": "import beta.helper\n# see beta/helper.py\n",
            "beta/helper.py": "value = 1\n",
        }, child_tree([
            feature("alpha", "alpha", entries=[]),
            feature("beta", "beta", entries=[]),
        ]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["alpha"]["uses"], [{"feature": "beta", "by": "import"}])

    def test_mention_finds_paths_and_other_directory_names(self):
        repository = make_repo(self, {
            "alpha/guide.md": "See beta/helper.py and the beta directory.\n",
            "beta/helper.py": "value = 1\n",
        }, child_tree([
            feature("alpha", None, entries=["alpha/guide.md"]),
            feature("beta", "beta", entries=[]),
        ]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["alpha"]["uses"], [{"feature": "beta", "by": "mention"}])

    def test_nested_path_mention_does_not_match_a_root_level_file(self):
        repository = make_repo(self, {
            "README.md": "Root guide.\n",
            "xy-goal/README.md": "Goal guide.\n",
            "xy-workflow/SKILL.md": "See xy-goal/README.md.\n",
        }, child_tree([
            feature("root-guide", None, entries=["README.md"]),
            feature("goal", "xy-goal"),
            feature("workflow", "xy-workflow"),
        ]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["workflow"]["uses"], [
            {"feature": "goal", "by": "mention"},
        ])

    def test_root_directory_name_uses_the_primary_checkout_from_a_worktree(self):
        with tempfile.TemporaryDirectory() as temporary:
            primary = Path(temporary) / "primary-checkout-name"
            worktree = Path(temporary) / "linked-worktree-name"
            primary.mkdir()
            (primary / "README.md").write_text("Root.\n", encoding="utf-8")
            feature_path = primary / "repo-map" / "features.json"
            feature_path.parent.mkdir()
            feature_path.write_text(json.dumps(feature("root", "")), encoding="utf-8")
            subprocess.run(["git", "-C", str(primary), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(primary), "config", "user.name", "Fixture"], check=True)
            subprocess.run(["git", "-C", str(primary), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(primary), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(primary), "commit", "-qm", "fixture"], check=True)
            subprocess.run(
                ["git", "-C", str(primary), "worktree", "add", "--quiet", "--detach", str(worktree), "HEAD"],
                check=True,
            )
            data = build_map(self, worktree)

        self.assertEqual(data["features"]["root"]["directory_name"], "primary-checkout-name")

    def test_reports_each_feature_marker_and_marker_count(self):
        package = feature("package", "pkg", children=[
            feature("alpha", None, entries=[], source="起草"),
            feature("future", None, entries=[], source="起草"),
        ])
        repository = make_repo(self, {"pkg/orphan.py": "unused = 1\n"}, child_tree([package]))
        data = build_map(self, repository)
        self.assertIn("等你拍板", data["features"]["alpha"]["markers"])
        self.assertIn("还没实现", data["features"]["future"]["markers"])
        self.assertIn("没归到功能的代码", data["features"]["package"]["markers"])
        self.assertEqual(data["markers"], {"等你拍板": 2, "还没实现": 2, "没归到功能的代码": 1})


if __name__ == "__main__":
    unittest.main()
