"""The cleanup fixture can be materialized as a tracked temporary Git repository."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parents[1]
sys.path.insert(0, str(TESTS.parent / "test_data"))
sys.path.insert(0, str(REPO_ROOT / "xy-repo-map"))

from create_fixture import create_fixture_repo
from repo_map_lib.checker import check_repository


class FixtureRepositoryTest(unittest.TestCase):
    def test_builder_creates_a_git_repository_with_a_repo_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "review-target"
            repository = create_fixture_repo(destination)
            tracked = subprocess.run(
                ["git", "-C", str(repository), "ls-files"], check=True,
                capture_output=True, text=True
            ).stdout.splitlines()
            self.assertTrue((repository / "repo-map" / "model.c4").is_file())
            self.assertIn("repo-map/model.c4", tracked)
            self.assertIn("src/cleanup_fixture/core.py", tracked)
            self.assertFalse((repository / "fixture_expected_findings.md").exists())

    def test_known_problem_ledger_is_outside_the_repository_template(self):
        fixture = TESTS.parent / "test_data" / "fixture_repo"
        ledger = TESTS / "fixture_expected_findings.md"
        self.assertTrue(fixture.is_dir())
        self.assertTrue(ledger.is_file())
        source = (fixture / "src" / "cleanup_fixture" / "core.py").read_text(encoding="utf-8")
        self.assertIn("unused_checksum", source)
        self.assertIn("def normalize_label", source)
        self.assertIn("def clean_label", source)
        self.assertIn("legacy-null", source)
        self.assertIn("retry_count += 1", source)
        self.assertIn("--legacy-export", (fixture / "src" / "cleanup_fixture" / "cli.py").read_text(encoding="utf-8"))

    def test_fixture_graph_passes_checks_without_an_approval_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = create_fixture_repo(Path(temporary) / "review-target")
            problems, graph = check_repository(repository, False)
            self.assertEqual(problems, [], problems)
            self.assertIn("cleanup_fixture.core", graph["elements"])


if __name__ == "__main__":
    unittest.main()
