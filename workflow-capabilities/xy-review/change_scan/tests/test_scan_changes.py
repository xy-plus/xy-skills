import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SCAN_DIRECTORY = Path(__file__).resolve().parents[1]
SCANNER = SCAN_DIRECTORY / "scan_changes.py"
sys.path.insert(0, str(SCAN_DIRECTORY))
import scan_changes  # noqa: E402


class ScanChangesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")

    def git(self, *arguments, cwd=None):
        return subprocess.run(
            ["git", "-C", str(cwd or self.repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def write(self, relative_path, content):
        path = self.repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD")

    def scan(self, base, repository=None, env=None):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCANNER),
                "--repo",
                str(repository or self.repository),
                "--base",
                base,
            ],
            capture_output=True,
            text=True,
            env=env,
        )

    def scan_document(self, base):
        result = self.scan(base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_scan_rejects_new_existing_cycle_import_entries(self):
        self.write("repo-map/skip.json", "[]\n")
        manifest = {
            "id": "root",
            "directory": "",
            "existing_cycle_imports": [{"from": "alpha", "to": "beta"}],
            "children": [
                {"id": "alpha", "directory": "alpha", "children": []},
                {"id": "beta", "directory": "beta", "children": []},
            ],
        }
        self.write("repo-map/features.json", json.dumps(manifest))
        self.write("alpha/module.py", "value = 1\n")
        self.write("beta/module.py", "value = 2\n")
        base = self.commit("base")
        manifest["existing_cycle_imports"].append({"from": "beta", "to": "alpha"})
        self.write("repo-map/features.json", json.dumps(manifest))
        self.commit("add cycle registry entry")

        result = self.scan(base)
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1, output)
        self.assertIn("名单只能删不能加", output)
        self.assertIn("beta → alpha", output)

    def test_scan_skips_cycle_registry_comparison_without_a_feature_map(self):
        self.write("README.md", "Repository without a feature map.\n")
        self.write("repo-map/skip.json", "[]\n")
        base = self.commit("base")
        self.write("changed.md", "A small change.\n")
        self.commit("change")

        with patch.object(
            scan_changes,
            "cycle_registry_change_errors",
            side_effect=AssertionError("cycle registry comparison should be skipped"),
        ):
            result = self.scan(base)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_changed_paths_ignore_pure_renames_and_keep_added_modified_files(self):
        self.write("modified.md", "before\n")
        self.write("deleted.md", "remove\n")
        self.write("old-name.md", "rename\n")
        base = self.commit("base")

        self.write("modified.md", "after\n")
        (self.repository / "deleted.md").unlink()
        self.git("mv", "old-name.md", "new-name.md")
        self.write("added.md", "new\n")
        (self.repository / "link.md").symlink_to("added.md")
        self.commit("changes")

        document = self.scan_document(base)

        self.assertEqual(document["changed"], ["added.md", "modified.md"])

    def test_pure_rename_produces_no_changed_path_or_hits(self):
        repeated = [f"shared block row {number} carries enough words" for number in range(1, 9)]
        self.write("source.md", "\n".join(repeated) + "\n")
        self.write("old-name.md", "\n".join(repeated) + "\n")
        base = self.commit("base")
        self.git("mv", "old-name.md", "new-name.md")
        self.commit("rename")

        document = self.scan_document(base)

        self.assertEqual(document["changed"], [])
        self.assertEqual(document["hits"], [])

    def test_changed_rename_reports_new_duplicate_block_in_added_lines(self):
        repeated = [f"shared block row {number} carries enough words" for number in range(1, 9)]
        original = [f"filler row {number} remains in the same file" for number in range(1, 17)]
        original.extend(repeated)
        self.write("source.md", "\n".join(repeated) + "\n")
        self.write("old-name.md", "\n".join(original) + "\n")
        base = self.commit("base")
        self.git("mv", "old-name.md", "new-name.md")
        added_filler = [f"added filler row {number} remains distinct" for number in range(1, 9)]
        with open(self.repository / "new-name.md", "a", encoding="utf-8") as stream:
            stream.write("\n".join(added_filler + repeated) + "\n")
        self.commit("rename and append")

        document = self.scan_document(base)

        self.assertEqual(document["changed"], ["new-name.md"])
        self.assertNotIn("partial", document)
        new_hits = [
            hit for hit in document["hits"]
            if hit["tool"] == "duplicate-blocks" and hit["path"] == "new-name.md"
        ]
        self.assertEqual([hit["line"] for hit in new_hits], [33])

    def test_changed_rename_deletions_include_lines_on_both_sides(self):
        original = [f"stable line {number} has enough meaning" for number in range(1, 9)]
        self.write("old-name.md", "\n".join(original) + "\n")
        base = self.commit("base")
        self.git("mv", "old-name.md", "new-name.md")
        self.write("new-name.md", "\n".join(original[:2] + original[4:]) + "\n")
        self.commit("rename and delete")

        document = self.scan_document(base)

        self.assertEqual(document["changed"], ["new-name.md"])
        self.assertNotIn("partial", document)
        self.assertEqual(document["hits"], [])

    def test_deleted_files_and_gitlinks_are_not_changed_regular_files(self):
        self.write("deleted.md", "remove\n")
        base = self.commit("base")
        (self.repository / "deleted.md").unlink()
        self.write("regular.md", "new regular file\n")

        subrepository = self.root / "subrepository"
        subrepository.mkdir()
        subprocess.run(["git", "-C", str(subrepository), "init", "--quiet"], check=True)
        subprocess.run(
            ["git", "-C", str(subrepository), "config", "user.name", "Test"], check=True
        )
        subprocess.run(
            ["git", "-C", str(subrepository), "config", "user.email", "test@example.com"],
            check=True,
        )
        (subrepository / "file.txt").write_text("subrepository\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(subrepository), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(subrepository), "commit", "--quiet", "-m", "subrepository"],
            check=True,
        )
        subrepository_head = subprocess.run(
            ["git", "-C", str(subrepository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.git("add", "-A")
        self.git("update-index", "--add", "--cacheinfo", "160000", subrepository_head, "module")
        self.commit("delete and gitlink")

        document = self.scan_document(base)

        self.assertEqual(document["changed"], ["regular.md"])

    def test_dirty_and_untracked_worktrees_are_rejected(self):
        self.write("tracked.md", "base\n")
        base = self.commit("base")
        self.write("untracked.md", "not committed\n")

        result = self.scan(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("工作树", result.stdout + result.stderr)

        (self.repository / "untracked.md").unlink()
        self.write("tracked.md", "working change\n")
        result = self.scan(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("工作树", result.stdout + result.stderr)

    def test_base_must_be_an_ancestor_and_must_differ_from_head(self):
        self.write("base.md", "base\n")
        self.commit("base")
        self.git("switch", "-c", "side")
        self.write("side.md", "side\n")
        side = self.commit("side")
        self.git("switch", "-")
        self.write("main.md", "main\n")
        head = self.commit("main")

        same = self.scan(head)
        unrelated = self.scan(side)

        self.assertNotEqual(same.returncode, 0)
        self.assertIn("--base", same.stdout + same.stderr)
        self.assertNotEqual(unrelated.returncode, 0)
        self.assertIn("祖先", unrelated.stdout + unrelated.stderr)

    def test_repository_argument_must_name_the_repository_root(self):
        self.write("inside/file.md", "base\n")
        base = self.commit("base")

        result = self.scan(base, self.repository / "inside")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("仓库根", result.stdout + result.stderr)

        relative = self.scan(base, ".")
        self.assertNotEqual(relative.returncode, 0)
        self.assertIn("绝对路径", relative.stdout + relative.stderr)

    def test_skip_list_excludes_its_paths_from_changed_files(self):
        self.write("repo-map/skip.json", '["hidden.md"]\n')
        self.write("hidden.md", "before\n")
        self.write("shown.md", "before\n")
        base = self.commit("base")
        self.write("hidden.md", "after\n")
        self.write("shown.md", "after\n")
        self.commit("changes")

        document = self.scan_document(base)

        self.assertEqual(document["changed"], ["shown.md"])

    def test_changed_paths_use_the_base_skip_list(self):
        self.write("repo-map/skip.json", '["changed.py"]\n')
        self.write("changed.py", "import os\n")
        base = self.commit("base")
        self.write("repo-map/skip.json", "[]\n")
        self.write("changed.py", "import os\nvalue = 1\n")
        self.commit("remove skip entry")

        document = self.scan_document(base)

        self.assertNotIn("changed.py", document["changed"])
        self.assertFalse(any(hit["path"] == "changed.py" for hit in document["hits"]))

    def test_same_commits_produce_stable_json_and_full_head(self):
        self.write("readme.md", "before\n")
        base = self.commit("base")
        self.write("readme.md", "after\n")
        self.commit("changes")

        first = self.scan_document(base)
        second = self.scan_document(base)

        self.assertEqual(first, second)
        self.assertRegex(first["head"], r"^[0-9a-f]{40}$")
        self.assertEqual(first["repo"], str(self.repository.resolve()))

    def test_hit_id_collisions_get_numeric_suffixes(self):
        self.write("changed.md", "before\n")
        base = self.commit("base")
        self.write("changed.md", "after\n")
        self.commit("change")
        duplicate_hits = [
            scan_changes._stable_hit("example", "rule", "changed.md", (1, 3), message)
            for message in ("first", "second")
        ]
        self.assertEqual(duplicate_hits[0]["lines"], [1, 3])

        with (
            patch.object(scan_changes, "_duplicate_block_hits", return_value=duplicate_hits),
            patch.object(scan_changes, "_line_count_hits", return_value=[]),
            patch.object(scan_changes, "_ruff_hits", return_value=[]),
            patch.object(scan_changes, "_structural_test_hits", return_value=[]),
        ):
            hits = scan_changes.scan_repository(str(self.repository), base)["hits"]

        self.assertEqual(
            [hit["id"] for hit in hits],
            ["example:rule:changed.md:1", "example:rule:changed.md:1:2"],
        )

    def test_line_limit_applies_only_above_1000_to_changed_code_docs_and_tests(self):
        self.write("base.md", "unchanged\n")
        base = self.commit("base")
        self.write("exact.md", "line\n" * 1000)
        self.write("over.md", "line\n" * 1001)
        self.write("data.json", "{}\n" * 1001)
        self.commit("large files")

        hits = self.scan_document(base)["hits"]

        self.assertEqual(
            [(hit["tool"], hit["path"], hit["line"]) for hit in hits],
            [("file-lines", "over.md", 1001)],
        )
        self.assertEqual(
            hits[0]["id"], "file-lines:over-1000-lines:over.md:1001"
        )
        self.assertEqual(hits[0]["lines"], [1001, 1001])

    def duplicate_document(self, path, meaningful_lines):
        self.write("source.md", "".join(f"{line}\n" for line in meaningful_lines))
        base = self.commit("source")
        copy = "\n!!!\n" + "\n".join(meaningful_lines) + "\n"
        self.write(path, copy)
        self.commit("copy")
        return self.scan_document(base)

    def test_duplicate_blocks_count_six_meaningful_lines_and_merge_adjacent_windows(self):
        lines = [f"meaningful line {number} carries words" for number in range(1, 9)]

        document = self.duplicate_document("copy.md", lines)

        hits = [hit for hit in document["hits"] if hit["tool"] == "duplicate-blocks"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["path"], "copy.md")
        self.assertEqual(hits[0]["line"], 3)
        self.assertIn("source.md:1-8", hits[0]["message"])

    def test_duplicate_blocks_ignore_blank_and_punctuation_only_lines(self):
        lines = [f"shared content row {number} has words" for number in range(1, 7)]

        document = self.duplicate_document("copy.md", lines)

        hits = [hit for hit in document["hits"] if hit["tool"] == "duplicate-blocks"]
        self.assertEqual(len(hits), 1)

    def test_duplicate_blocks_ignore_python_and_rust_import_only_lines(self):
        lines = [
            "import package_one.module_one",
            "from package_two import helper_two",
            "use crate::module_three::item_three;",
            "import package_four.module_four",
            "from package_five import helper_five",
            "use crate::module_six::item_six;",
        ]

        document = self.duplicate_document("copy.md", lines)

        self.assertFalse(
            any(hit["tool"] == "duplicate-blocks" for hit in document["hits"])
        )

    def test_duplicate_blocks_count_code_lines_across_import_lines(self):
        code_lines = [
            f"shared operation row {number} carries the same meaningful implementation"
            for number in range(1, 7)
        ]
        source = [
            line
            for number, code in enumerate(code_lines, start=1)
            for line in (code, f"import source_helpers.module_{number}")
        ]
        copy = [
            line
            for number, code in enumerate(code_lines, start=1)
            for line in (code, f"from copy_helpers_{number} import helper_{number}")
        ]
        self.write("source.md", "\n".join(source) + "\n")
        base = self.commit("source")
        self.write("copy.md", "\n".join(copy) + "\n")
        self.commit("copy")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "duplicate-blocks"
        ]

        self.assertEqual(len(hits), 1, hits)
        self.assertEqual((hits[0]["path"], hits[0]["line"]), ("copy.md", 1))
        self.assertIn("source.md:1-11", hits[0]["message"])

    def test_duplicate_blocks_do_not_report_five_lines(self):
        lines = [f"shared content row {number} has words" for number in range(1, 6)]

        document = self.duplicate_document("copy.md", lines)

        self.assertFalse(
            any(hit["tool"] == "duplicate-blocks" for hit in document["hits"])
        )

    def test_duplicate_blocks_require_forty_alphanumeric_characters(self):
        lines = [f"row {number}" for number in range(1, 7)]

        document = self.duplicate_document("copy.md", lines)

        self.assertFalse(
            any(hit["tool"] == "duplicate-blocks" for hit in document["hits"])
        )

    def test_changed_todo_files_are_scanned_for_duplicate_blocks(self):
        lines = [f"shared content row {number} has enough words" for number in range(1, 7)]
        self.write("source.md", "\n".join(lines) + "\n")
        base = self.commit("source")
        self.write("TODO.md", "\n".join(lines) + "\n")
        self.commit("todo")

        document = self.scan_document(base)

        duplicate_hits = [hit for hit in document["hits"] if hit["tool"] == "duplicate-blocks"]
        self.assertEqual([hit["path"] for hit in duplicate_hits], ["TODO.md"])

    def test_ruff_uses_selected_rules_without_repository_configuration(self):
        self.write("pyproject.toml", "[tool.ruff]\nselect = []\n")
        self.write("clean.py", "import json\n")
        base = self.commit("base")
        self.write("changed.py", "import os\n")
        self.commit("python change")

        hits = self.scan_document(base)["hits"]

        self.assertTrue(
            any(hit["tool"] == "ruff" and "F401" in hit["message"] for hit in hits),
            hits,
        )
        self.assertFalse(any(hit["path"] == "clean.py" for hit in hits), hits)

    def test_renamed_file_matches_unused_imports_by_name(self):
        self.write("old/module.py", "from package import HOUR\n")
        base = self.commit("base")
        (self.repository / "new").mkdir()
        self.git("mv", "old/module.py", "new/module.py")
        self.write(
            "new/module.py",
            "from package import HOUR\nfrom package import DAY\n",
        )
        self.commit("rename and change imports")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "ruff" and "F401" in hit["message"]
        ]

        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0]["path"], "new/module.py")
        self.assertEqual(hits[0]["line"], 2)
        self.assertIn("DAY", hits[0]["message"])

    def test_unchanged_unused_local_is_suppressed_but_new_name_is_reported(self):
        self.write(
            "changed.py",
            "def calculate():\n    unused = 1\n    return 0\n",
        )
        base = self.commit("base")
        self.write(
            "changed.py",
            "def calculate():\n    unused = 2\n    new_unused = 3\n    return 0\n",
        )
        self.commit("add unused local")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "ruff" and "F841" in hit["message"]
        ]

        self.assertEqual(len(hits), 1, hits)
        self.assertIn("new_unused", hits[0]["message"])

    def test_existing_complexity_is_suppressed_for_the_same_function_after_rename(self):
        source = "def complex_function(value):\n"
        source += "".join(
            f"    if value == {number}: value += 1\n" for number in range(11)
        )
        source += "    return value\n"
        self.write("old/module.py", source)
        base = self.commit("base")
        (self.repository / "new").mkdir()
        self.git("mv", "old/module.py", "new/module.py")
        self.write("new/module.py", source.replace("value == 0", "value == 100"))
        self.commit("rename and change condition")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "ruff" and "C901" in hit["message"]
        ]

        self.assertEqual(hits, [])

    def test_existing_structural_test_is_suppressed_by_test_name_after_rename(self):
        source = (
            "def test_same_name():\n    assert process(1) == 2\n"
            "def test_peer():\n    assert process(3) == 4\n"
        )
        self.write("tests/old_test.py", source)
        base = self.commit("base")
        self.git("mv", "tests/old_test.py", "tests/new_test.py")
        self.write("tests/new_test.py", source.replace("process(1)", "process(8)"))
        self.commit("rename and change literal")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "structural-tests"
        ]

        self.assertEqual(hits, [])

    def test_existing_file_length_finding_is_suppressed_after_content_change(self):
        source = "".join(
            f"distinct line {number} with enough words to avoid duplicates\n"
            for number in range(1001)
        )
        self.write("large.md", source)
        base = self.commit("base")
        self.write("large.md", source.replace("distinct line 0", "changed line 0", 1))
        self.commit("change long file")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "file-lines"
        ]

        self.assertEqual(hits, [])

    def test_comment_out_code_matches_only_the_same_unchanged_line(self):
        self.write("changed.py", "# total = 1\nvalue = 1\n")
        base = self.commit("base")
        self.write("changed.py", "# total = 1\n# fresh_total = 2\nvalue = 2\n")
        self.commit("add commented code")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "ruff" and "ERA001" in hit["message"]
        ]

        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0]["line"], 2)

    def test_moved_commented_code_is_suppressed_by_same_line_text(self):
        self.write("changed.py", "# total = 1\nvalue = 1\nkeep = 1\n")
        base = self.commit("base")
        self.write("changed.py", "value = 2\nkeep = 1\n# total = 1\n")
        self.commit("move commented code")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "ruff" and "ERA001" in hit["message"]
        ]

        self.assertEqual(hits, [])

    def test_only_one_of_multiple_same_text_commented_lines_is_suppressed(self):
        self.write("changed.py", "# total = 1  \nvalue = 1\nkeep = 1\n")
        base = self.commit("base")
        self.write(
            "changed.py",
            "value = 2\nkeep = 1\n# total = 1\n# total = 1\n",
        )
        self.commit("move and duplicate commented code")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "ruff" and "ERA001" in hit["message"]
        ]

        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0]["line"], 4)

    def test_unchanged_duplicate_block_is_suppressed_when_another_line_changes(self):
        repeated = [f"shared block row {number} carries enough words" for number in range(1, 9)]
        self.write("source.md", "\n".join(repeated) + "\n")
        self.write("copy.md", "old lead line is distinct\n" + "\n".join(repeated) + "\n")
        base = self.commit("base with old duplicate")
        self.write("copy.md", "new lead line is distinct\n" + "\n".join(repeated) + "\n")
        self.commit("change line before duplicate")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "duplicate-blocks" and hit["path"] == "copy.md"
        ]

        self.assertEqual(hits, [])

    def test_duplicate_block_with_changed_line_is_reported(self):
        original = [f"shared block row {number} carries enough words" for number in range(1, 9)]
        changed = original.copy()
        changed[3] = "changed shared row carries enough words to stay duplicated"
        self.write("source.md", "\n".join(original) + "\n")
        self.write("copy.md", "\n".join(original) + "\n")
        base = self.commit("base with old duplicate")
        self.write("source.md", "\n".join(changed) + "\n")
        self.write("copy.md", "\n".join(changed) + "\n")
        self.commit("change duplicate block")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "duplicate-blocks" and hit["path"] == "copy.md"
        ]

        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0]["lines"], [1, 8])

    def test_one_old_duplicate_hit_matches_at_most_one_new_hit(self):
        repeated = [f"shared block row {number} carries enough words" for number in range(1, 21)]
        self.write("source.md", "\n".join(repeated) + "\n")
        self.write("copy.md", "\n".join(repeated) + "\n")
        base = self.commit("base with old duplicate")
        changed = repeated.copy()
        changed[9] = "changed row breaks duplicate windows but keeps enough words"
        self.write("copy.md", "\n".join(changed) + "\n")
        self.commit("split old duplicate")

        hits = [
            hit for hit in self.scan_document(base)["hits"]
            if hit["tool"] == "duplicate-blocks" and hit["path"] == "copy.md"
        ]

        self.assertEqual(len(hits), 1, hits)

    def test_ambiguous_equal_overlap_with_old_duplicate_hits_is_reported(self):
        old_hits = [
            scan_changes._stable_hit(
                "duplicate-blocks", "six-lines-forty-characters", "old.md",
                (1, 6), "first old block",
            ),
            scan_changes._stable_hit(
                "duplicate-blocks", "six-lines-forty-characters", "old.md",
                (7, 12), "second old block",
            ),
        ]
        new_hit = scan_changes._stable_hit(
            "duplicate-blocks", "six-lines-forty-characters", "new.md",
            (1, 12), "overlaps both old blocks equally",
        )

        comparison = scan_changes._IssueComparison(
            self.repository,
            self.repository,
            {"new.md": "old.md"},
            {"new.md": [(1, 12, 1)]},
        )
        new_issues = scan_changes._new_issue_hits(old_hits, [new_hit], comparison)

        self.assertEqual(new_issues, [new_hit])

    def test_ruff_function_rules_report_the_function_definition_range(self):
        self.write("base.py", "value = 1\n")
        base = self.commit("base")
        self.write(
            "changed.py",
            "def complex_function(value):\n"
            "    if value == 0: value += 1\n"
            "    if value == 1: value += 1\n"
            "    if value == 2: value += 1\n"
            "    if value == 3: value += 1\n"
            "    if value == 4: value += 1\n"
            "    if value == 5: value += 1\n"
            "    if value == 6: value += 1\n"
            "    if value == 7: value += 1\n"
            "    if value == 8: value += 1\n"
            "    if value == 9: value += 1\n"
            "    if value == 10: value += 1\n"
            "    return value\n",
        )
        self.commit("complex function")

        hits = self.scan_document(base)["hits"]
        complexity = [hit for hit in hits if hit["tool"] == "ruff" and "C901" in hit["message"]]

        self.assertEqual(len(complexity), 1, hits)
        self.assertEqual(complexity[0]["lines"], [1, 13])

    def test_missing_python_tools_are_errors(self):
        self.write("before.py", "value = 1\n")
        base = self.commit("base")
        self.write("before.py", "value = 2\n")
        self.commit("python change")
        environment = dict(os.environ)
        environment["PATH"] = "/usr/bin:/bin"

        result = self.scan(base, env=environment)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ruff", result.stdout + result.stderr)

    def test_python_structural_tests_ignore_function_names_but_keep_body_names(self):
        self.write(
            "test_old.py",
            "def test_old_name():\n    assert process(1) == 'old'\n"
            "def test_distinct_call():\n    assert process_old(2) == 'old'\n",
        )
        base = self.commit("base")
        self.write(
            "test_new.py",
            "def test_new_name():\n    assert process(8) == 'new'\n"
            "def test_distinct_body_name():\n    assert process_new(9) == 'new'\n",
        )
        self.commit("python tests")

        hits = self.scan_document(base)["hits"]
        structural = [hit for hit in hits if hit["tool"] == "structural-tests"]

        self.assertEqual(len(structural), 1, hits)
        self.assertEqual(structural[0]["path"], "test_new.py")
        self.assertEqual(structural[0]["lines"], [1, 2])
        self.assertIn("test_old.py", structural[0]["message"])

    def _rust_structural_hits(self, package, library, old_tests, new_tests):
        self.write(
            "Cargo.toml",
            f'[package]\nname = "{package}"\nversion = "0.1.0"\nedition = "2021"\n',
        )
        self.write("src/lib.rs", library)
        self.write("src/old_tests.rs", old_tests)
        base = self.commit("base")
        self.write("src/new_tests.rs", new_tests)
        self.commit("rust tests")
        hits = self.scan_document(base)["hits"]
        return [hit for hit in hits if hit["tool"] == "structural-tests"]

    def _rust_name_structure_hits(self):
        return self._rust_structural_hits(
            "test_fixture",
            "pub fn process(value: i32) -> i32 { value }\n"
            "pub fn process_old(value: i32) -> i32 { value }\n"
            "pub fn process_new(value: i32) -> i32 { value }\n"
            "#[cfg(test)]\nmod old_tests;\n#[cfg(test)]\nmod new_tests;\n",
            "#[test]\nfn old_test_name() {\n"
            "    // { old comment\n"
            "    let _ = r#\"old { string }\"#;\n"
            "    let _ = '}';\n"
            "    assert_eq!(super::process(1), 2);\n}\n"
            "#[test]\nfn same_assertion_other_call() { assert_eq!(super::process_old(3), 4); }\n",
            "#[test]\nfn new_test_name() {\n"
            "    // } new comment\n"
            "    let _ = r#\"new { string }\"#;\n"
            "    let _ = '{';\n"
            "    assert_eq!(super::process(8), 9);\n}\n"
            "#[test]\nfn changed_call_name() { assert_eq!(super::process_new(5), 6); }\n",
        )

    def _rust_boolean_literal_hits(self):
        return self._rust_structural_hits(
            "boolean_fixture",
            "#[cfg(test)]\nmod old_tests;\n#[cfg(test)]\nmod new_tests;\n",
            "#[test]\nfn old_boolean_value() { let _value = true; }\n",
            "#[test]\nfn new_boolean_value() { let _value = false; }\n",
        )

    def test_rust_structural_tests_ignore_function_names_but_keep_body_names(self):
        structural = self._rust_name_structure_hits()
        self.assertEqual(len(structural), 1, structural)
        self.assertEqual(structural[0]["path"], "src/new_tests.rs")
        self.assertIn("src/old_tests.rs", structural[0]["message"])

    def test_rust_structural_tests_treat_booleans_as_literals(self):
        structural = self._rust_boolean_literal_hits()
        self.assertEqual(len(structural), 1, structural)
        self.assertEqual(structural[0]["path"], "src/new_tests.rs")
        self.assertIn("src/old_tests.rs", structural[0]["message"])


if __name__ == "__main__":
    unittest.main()
