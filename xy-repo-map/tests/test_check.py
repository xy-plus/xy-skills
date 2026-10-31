"""Repository-wide check behavior without requiring npx or LikeC4."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.constants import STANDARD_KINDS
from repo_map_lib.structure import structure_digest
from repo_map_lib.checker import check_repository


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", str(self.root)], check=True, capture_output=True)
        (self.root / "README.md").write_text("intro\n", encoding="utf-8")
        self.repo_map = self.root / "repo-map"
        self.repo_map.mkdir()
        (self.repo_map / "likec4.config.json").write_text('{"name":"sample"}\n', encoding="utf-8")
        (self.repo_map / "kinds.c4").write_text(STANDARD_KINDS, encoding="utf-8")
        (self.repo_map / "model.c4").write_text(
            "model {\n  sample = node 'Sample' {\n    description 'A repository.'\n"
            "    link ../README.md 'docs'\n  }\n}\n", encoding="utf-8")
        (self.repo_map / "approvals.txt").write_text("", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Fixture"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "fixture@example.invalid"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-m", "unapproved map"],
                       check=True, capture_output=True)
        self.model = {"elements": {
            "sample": {"id": "sample", "kind": "node", "title": "Sample",
                       "description": {"txt": "A repository."}, "style": {},
                       "links": [{"url": "../README.md", "title": "docs"}]},
        }, "relations": {}}
        self.validation = subprocess.CompletedProcess([], 0, "", "")

    def checked(self, approval_text):
        (self.repo_map / "approvals.txt").write_text(approval_text, encoding="utf-8")
        with patch("repo_map_lib.checker.export_model", return_value=self.model):
            return check_repository(self.root, True)

    def test_check_accepts_a_matching_record_and_rejects_an_unapproved_committed_map(self):
        digest = structure_digest(self.model)
        approved = f"repo-map sha256 {digest} 2026-10-31 /run/xy-goal/quotes/1.md\n"
        problems, _, owners, tracked = self.checked(approved)
        self.assertEqual(problems, [])
        self.assertEqual(owners["README.md"].element, "sample")
        self.assertIn("README.md", tracked)

        problems, _, _, _ = self.checked("repo-map sha256 " + "b" * 64 +
                                         " 2026-10-31 /run/xy-goal/quotes/1.md\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("图未经同意", problems[0])
        self.assertIn(digest, problems[0])

    def test_check_rejects_config_name_and_standard_kind_mismatches(self):
        (self.repo_map / "likec4.config.json").write_text('{"name":"other"}\n', encoding="utf-8")
        problems, _, _, _ = self.checked("")
        self.assertTrue(any("name" in problem for problem in problems), problems)

        (self.repo_map / "likec4.config.json").write_text('{"name":"sample"}\n', encoding="utf-8")
        (self.repo_map / "kinds.c4").write_text("specification {}\n", encoding="utf-8")
        problems, _, _, _ = self.checked("")
        self.assertTrue(any("kinds.c4" in problem for problem in problems), problems)

    def test_check_rejects_unsupported_code_file_suffixes(self):
        bad_model = {"elements": {
            "sample": {"id": "sample", "kind": "node", "title": "Sample",
                       "description": {"txt": "A repository."}, "style": {},
                       "links": [{"url": "../README.md", "title": "code"}]},
        }, "relations": {}}
        with patch("repo_map_lib.checker.export_model", return_value=bad_model):
            problems, _, _, _ = check_repository(self.root, False)
        self.assertTrue(any("后缀不支持" in problem and "README.md" in problem
                            for problem in problems), problems)

    def test_check_uses_validate_failure_even_if_export_would_succeed(self):
        validation_error = "LikeC4 validate 失败：syntax error"
        with patch("repo_map_lib.checker.export_model",
                   side_effect=ValueError(validation_error)) as export:
            problems, model, owners, tracked = check_repository(self.root, False)
        self.assertEqual(problems, [validation_error])
        self.assertIsNone(model)
        self.assertEqual(owners, {})
        self.assertEqual(tracked, [])
        export.assert_called_once()


if __name__ == "__main__":
    unittest.main()
