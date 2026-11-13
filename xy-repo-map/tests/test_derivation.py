import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from map_fixture import COMMAND, build_map, feature, make_repo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import repo_map_lib.map_data as map_data  # noqa: E402


def child_tree(children, entries=None):
    return feature(
        "root", "", subrepositories=[], external_dependencies=[],
        children=children, entries=entries,
    )


def build_map_with_sources(repository):
    sources = {}
    count_file = map_data.count_file

    def record_source(path, category, source):
        sources[path] = (category, source)
        return count_file(path, category, source)

    with patch.object(map_data, "count_file", record_source):
        data = map_data.build_map(repository)
    return data, sources


class DerivationTest(unittest.TestCase):
    def test_doc_characters_count_markdown_and_code_documentation_only(self):
        repository = make_repo(self, {
            "README.md": "A B\n",
            "pkg/module.py": '"""AB\n C"""\n# comment\nvalue = 1\n',
            "pkg/api.rs": "/// AB\n/// C\npub fn visible() {}\n// normal comment\n",
            "pkg/guide.md": " A B \n\nC D\n",
            "pkg/tests/test_docs.py": '"""TEST DOCS"""\n# test comment\n',
        }, feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("package", "pkg", children=[
                feature("module", None, entries=["pkg/module.py"]),
                feature("api", None, entries=["pkg/api.rs"]),
                feature("guide", None, entries=["pkg/guide.md"]),
            ]),
        ]))

        data = build_map(self, repository)
        features = data["features"]

        self.assertEqual(features["root"].get("doc_chars"), {"own": 2, "total": 24})
        self.assertEqual(features["package"].get("doc_chars"), {"own": 0, "total": 22})
        self.assertEqual(features["module"].get("doc_chars"), {"own": 9, "total": 9})
        self.assertEqual(features["api"].get("doc_chars"), {"own": 9, "total": 9})
        self.assertEqual(features["guide"].get("doc_chars"), {"own": 4, "total": 4})

    def test_file_category_matches_build_map_categories_and_line_totals(self):
        repository = make_repo(self, {
            "src/main.py": "value = 1\n",
            "src/target.py": "target = 1\n",
            "docs/guide.md": "# Guide\nA page.\n",
            "tests/test_parser.py": "value = 2\n",
            "assets/data.json": "{}\n",
            "assets/link.py": "placeholder\n",
            "assets/binary.py": "placeholder\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[]))
        link = repository / "assets/link.py"
        link.unlink()
        link.symlink_to("../src/target.py")
        (repository / "assets/binary.py").write_bytes(b"\0binary data\n")

        data = map_data.build_map(repository)

        self.assertEqual(data["features"]["root"]["lines"]["own"], {
            "code": 2, "docs": 2, "comments": 0, "tests": 1,
        })

    def test_derives_sorted_unique_external_uses_from_owned_code_and_documentation(self):
        repository = make_repo(self, {
            "src/run.py": "# Claude Code and superpowers; superpowers again.\nvalue = 1\n",
            "docs/guide.md": "web-portal and Claude Code.\n",
        }, feature(
            "root", "", subrepositories=[],
            external_dependencies=["web-portal", "Claude Code", "superpowers", "web-portal"],
            children=[feature("code", "src"), feature("guide", "docs")],
        ))

        data = build_map(self, repository)

        self.assertEqual(data["features"]["root"]["external_uses"], [])
        self.assertEqual(
            data["features"]["code"]["external_uses"],
            ["Claude Code", "superpowers"],
        )
        self.assertEqual(
            data["features"]["guide"]["external_uses"],
            ["Claude Code", "web-portal"],
        )

    def test_test_files_and_longer_words_do_not_satisfy_external_mentions(self):
        repository = make_repo(self, {
            "README.md": "web-portals are listed here.\n",
            "tests/test_names.py": "# web-portal\n",
        }, feature(
            "root", "", subrepositories=[], external_dependencies=["web-portal"],
        ))

        with self.assertRaisesRegex(ValueError, "外部依赖没有被任何代码或文档提到：web-portal"):
            map_data.build_map(repository)

    def test_classifies_source_documentation_tests_and_other_files(self):
        repository = make_repo(self, {
            "src/code.py": '"""module docs"""\n# comment\nvalue = 1\n',
            "src/view.html": "<!-- page note -->\n<p>hello</p>\n<div><!-- inline --></div>\n",
            "docs/guide.md": "# Guide\nA page.\n",
            "tests/example.py": '"""test docs"""\n# test comment\n',
            "vendor/lib.py": "value = 2\n",
            "assets/data.json": '{"value": 1}\n',
        }, feature("root", "", subrepositories=[], external_dependencies=[]))
        data = build_map(self, repository)
        counts = data["features"]["root"]["lines"]["own"]
        self.assertEqual(counts, {"code": 3, "docs": 3, "comments": 2, "tests": 2})

    def test_classifies_rust_files_with_plural_test_names_as_tests(self):
        repository = make_repo(self, {
            "README.md": "Rust test file names are classified here.\n",
            "src/core.rs": "fn core() {}\n",
            "src/helper_tests.rs": "#[test]\nfn helper() {}\n",
            "src/tests.rs": "#[test]\nfn integration() {}\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[]))

        data = map_data.build_map(repository)

        self.assertEqual(data["features"]["root"]["lines"]["own"]["tests"], 4)

    def test_files_classified_as_other_are_not_read(self):
        repository = make_repo(self, {
            "assets/data.bin": "placeholder\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[]))
        other_path = repository / "assets/data.bin"
        read_bytes = Path.read_bytes

        def reject_other_file(path):
            if path == other_path:
                raise AssertionError(f"read an other file: {path}")
            return read_bytes(path)

        with patch.object(Path, "read_bytes", reject_other_file):
            data, sources = build_map_with_sources(repository)

        self.assertEqual(sources["assets/data.bin"], ("other", ""))
        self.assertEqual(data["features"]["root"]["lines"]["total"]["code"], 0)

    def test_symlinks_and_nul_files_are_other_and_count_zero_lines(self):
        repository = make_repo(self, {
            "assets/target.py": "value = 1\n",
            "assets/link.py": "placeholder\n",
            "assets/video.ts": "placeholder\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[]))
        symlink = repository / "assets/link.py"
        symlink.unlink()
        symlink.symlink_to("target.py")
        (repository / "assets/video.ts").write_bytes(b"\x00video data\n")

        data, sources = build_map_with_sources(repository)

        self.assertEqual(sources["assets/link.py"], ("other", ""))
        self.assertEqual(sources["assets/video.ts"], ("other", ""))
        self.assertEqual(data["features"]["root"]["lines"]["total"]["code"], 1)

    def test_unsupported_suffix_symlink_and_nul_files_are_other(self):
        repository = make_repo(self, {
            "assets/target.py": "value = 1\n",
            "assets/link.go": "placeholder\n",
            "assets/binary.go": "placeholder\n",
            "vendor/legacy.go": "package legacy\nvalue = 1\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[]))
        symlink = repository / "assets/link.go"
        symlink.unlink()
        symlink.symlink_to("target.py")
        (repository / "assets/binary.go").write_bytes(b"\x00video data\n")

        checked = subprocess.run(
            ["python3", "-B", str(COMMAND), "check", "--repo", str(repository)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

        data, sources = build_map_with_sources(repository)

        self.assertEqual(sources["assets/link.go"], ("other", ""))
        self.assertEqual(sources["assets/binary.go"], ("other", ""))
        self.assertEqual(sources["vendor/legacy.go"], ("other", ""))
        self.assertEqual(data["features"]["root"]["lines"]["total"],
                         {"code": 1, "docs": 0, "comments": 0, "tests": 0})

    def test_invalid_utf8_text_is_replaced_and_keeps_counts_and_dependencies(self):
        repository = make_repo(self, {
            "pkg/source.py": "import pkg.target\n# invalid text\n",
            "pkg/guide.md": "See pkg/target.py for details.\ninvalid text\n",
            "pkg/target.py": "value = 1\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("package", "pkg", children=[
                feature("source", None, entries=["pkg/source.py"]),
                feature("guide", None, entries=["pkg/guide.md"]),
                feature("target", None, entries=["pkg/target.py"]),
            ]),
        ]))
        (repository / "pkg/source.py").write_bytes(b"import pkg.target\n# invalid: \xff\n")
        (repository / "pkg/guide.md").write_bytes(
            b"See pkg/target.py for details.\ninvalid: \xff\n"
        )

        data, sources = build_map_with_sources(repository)

        self.assertEqual(sources["pkg/source.py"], ("code", "import pkg.target\n# invalid: \ufffd\n"))
        self.assertEqual(
            sources["pkg/guide.md"],
            ("docs", "See pkg/target.py for details.\ninvalid: \ufffd\n"),
        )
        self.assertEqual(data["features"]["source"]["lines"]["own"]["code"], 1)
        self.assertEqual(data["features"]["source"]["lines"]["own"]["comments"], 1)
        self.assertEqual(data["features"]["guide"]["lines"]["own"]["docs"], 2)
        self.assertEqual(data["features"]["source"]["uses"], [
            {"feature": "target", "by": "import"},
        ])
        self.assertEqual(data["features"]["guide"]["uses"], [
            {"feature": "target", "by": "mention"},
        ])

    def test_unreadable_tracked_file_fails_check_with_its_path(self):
        repository = make_repo(self, {
            "src/missing.py": "value = 1\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[]))
        (repository / "src/missing.py").unlink()

        result = subprocess.run(
            ["python3", "-B", str(COMMAND), "check", "--repo", str(repository)],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("src/missing.py", result.stdout + result.stderr)

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
            ["README.md", "repo-map/features.json", "repo-map/skip.json"],
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
        self.assertIn("没归到功能的代码或测试", data["features"]["package"]["markers"])

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

    def test_unregistered_test_follows_other_directory_import_when_no_local_import_exists(self):
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
        self.assertEqual(data["features"]["external"]["files"]["reached"], ["pkg/tests/test_alpha.py"])
        self.assertEqual(data["features"]["package"]["files"]["whole"], [])

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
            feature("alpha", "alpha"),
            feature("beta", "beta"),
        ]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["alpha"]["uses"], [{"feature": "beta", "by": "mention"}])

    def test_ambiguous_directory_name_does_not_create_directory_mentions(self):
        repository = make_repo(self, {
            "alpha/shared/main.py": "value = 1\n",
            "beta/shared/main.py": "value = 2\n",
            "observer/guide.md": "shared\n",
        }, child_tree([
            feature("alpha-shared", "alpha/shared"),
            feature("beta-shared", "beta/shared"),
            feature("observer", "observer"),
        ]))

        data = build_map(self, repository)

        self.assertEqual(data["features"]["observer"]["uses"], [])

    def test_mentions_from_ancestor_directory_do_not_create_uses(self):
        repository = make_repo(self, {
            "README.md": "See pkg/module.py and the pkg directory.\n",
            "pkg/module.py": "value = 1\n",
        }, child_tree([
            feature("guide", None, entries=["README.md"]),
            feature("package", "pkg"),
        ]))
        data = build_map(self, repository)
        self.assertEqual(data["features"]["guide"]["uses"], [])

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
            root_feature = feature(
                "root", "", subrepositories=[], external_dependencies=[],
            )
            feature_path.write_text(json.dumps(root_feature), encoding="utf-8")
            (feature_path.parent / "skip.json").write_text("[]\n", encoding="utf-8")
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
        self.assertIn("没归到功能的代码或测试", data["features"]["package"]["markers"])
        self.assertEqual(data["markers"], {"等你拍板": 2, "还没实现": 2, "没归到功能的代码或测试": 1})
