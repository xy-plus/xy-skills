import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPOSITORY = Path(__file__).resolve().parents[1]
SCANNER = REPOSITORY / "xy-review" / "scan_changes.py"
sys.path.insert(0, str(REPOSITORY / "xy-review"))
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

    def test_changed_paths_keep_added_modified_and_renamed_regular_files(self):
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

        self.assertEqual(
            document["changed"], ["added.md", "modified.md", "new-name.md"]
        )

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
            scan_changes._stable_hit("example", "rule", "changed.md", 1, message)
            for message in ("first", "second")
        ]

        with (
            patch.object(scan_changes, "_duplicate_block_hits", return_value=duplicate_hits),
            patch.object(scan_changes, "_line_count_hits", return_value=[]),
            patch.object(scan_changes, "_ruff_hits", return_value=[]),
            patch.object(scan_changes, "_vulture_hits", return_value=[]),
            patch.object(scan_changes, "_clippy_hits", return_value=[]),
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

    def test_duplicate_blocks_skip_todo_files(self):
        lines = [f"shared content row {number} has enough words" for number in range(1, 7)]
        self.write("TODO.md", "\n".join(lines) + "\n")
        base = self.commit("todo source")
        self.write("copy.md", "\n".join(lines) + "\n")
        self.commit("copy")

        document = self.scan_document(base)

        self.assertFalse(
            any(hit["tool"] == "duplicate-blocks" for hit in document["hits"])
        )

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

    def test_vulture_scans_all_python_files_but_reports_changed_dead_code_only(self):
        self.write("changed.py", "def used():\n    return 1\n\ndef unused():\n    return 2\n")
        self.write("caller.py", "from changed import used\nprint(used())\n")
        base = self.commit("base")
        self.write("changed.py", "def used():\n    return 3\n\ndef unused():\n    return 2\n")
        self.commit("python change")

        hits = self.scan_document(base)["hits"]
        vulture_messages = [hit["message"] for hit in hits if hit["tool"] == "vulture"]

        self.assertTrue(any("unused" in message for message in vulture_messages), hits)
        self.assertFalse(any("function 'used'" in message for message in vulture_messages), hits)

    def test_missing_python_tools_are_errors(self):
        self.write("before.py", "value = 1\n")
        base = self.commit("base")
        self.write("before.py", "value = 2\n")
        self.commit("python change")
        environment = dict(os.environ)
        environment["PATH"] = "/usr/bin:/bin"

        result = self.scan(base, env=environment)

        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stdout + result.stderr, r"ruff|vulture")

    def test_clippy_reports_changed_rust_warnings_but_not_other_workspace_files(self):
        self.write(
            "Cargo.toml",
            '[package]\nname = "scan_fixture"\nversion = "0.1.0"\nedition = "2021"\n',
        )
        self.write(
            "src/lib.rs",
            "pub mod other;\npub fn changed(values: &Vec<i32>) -> usize { values.len() }\n",
        )
        self.write(
            "src/other.rs",
            "pub fn unchanged(values: &Vec<i32>) -> usize {\n"
            "    if values.len() == 0 { 0 } else { values.len() }\n}\n",
        )
        base = self.commit("base")
        self.write(
            "src/lib.rs",
            "pub mod other;\npub fn changed(values: &Vec<i32>) -> usize { values.len() + 1 }\n",
        )
        self.commit("rust change")

        hits = self.scan_document(base)["hits"]
        clippy_hits = [hit for hit in hits if hit["tool"] == "cargo-clippy"]

        self.assertTrue(any(hit["path"] == "src/lib.rs" for hit in clippy_hits), hits)
        self.assertFalse(any(hit["path"] == "src/other.rs" for hit in clippy_hits), hits)

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
        self.assertIn("test_old.py", structural[0]["message"])

    def test_rust_structural_tests_ignore_function_names_but_keep_body_names(self):
        self.write(
            "Cargo.toml",
            '[package]\nname = "test_fixture"\nversion = "0.1.0"\nedition = "2021"\n',
        )
        self.write(
            "src/lib.rs",
            "pub fn process(value: i32) -> i32 { value }\n"
            "pub fn process_old(value: i32) -> i32 { value }\n"
            "pub fn process_new(value: i32) -> i32 { value }\n"
            "#[cfg(test)]\nmod old_tests;\n#[cfg(test)]\nmod new_tests;\n",
        )
        self.write(
            "src/old_tests.rs",
            "#[test]\nfn old_test_name() {\n"
            "    // { old comment\n"
            "    let _ = r#\"old { string }\"#;\n"
            "    let _ = '}';\n"
            "    assert_eq!(super::process(1), 2);\n}\n"
            "#[test]\nfn same_assertion_other_call() { assert_eq!(super::process_old(3), 4); }\n",
        )
        base = self.commit("base")
        self.write(
            "src/new_tests.rs",
            "#[test]\nfn new_test_name() {\n"
            "    // } new comment\n"
            "    let _ = r#\"new { string }\"#;\n"
            "    let _ = '{';\n"
            "    assert_eq!(super::process(8), 9);\n}\n"
            "#[test]\nfn changed_call_name() { assert_eq!(super::process_new(5), 6); }\n",
        )
        self.commit("rust tests")

        hits = self.scan_document(base)["hits"]
        structural = [hit for hit in hits if hit["tool"] == "structural-tests"]

        self.assertEqual(len(structural), 1, hits)
        self.assertEqual(structural[0]["path"], "src/new_tests.rs")
        self.assertIn("src/old_tests.rs", structural[0]["message"])


if __name__ == "__main__":
    unittest.main()
