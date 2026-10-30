"""Command-line argument and exit-code behavior."""
from pathlib import Path
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map import main


class CliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", str(self.root)], check=True, capture_output=True)

    def run_main(self, arguments):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(arguments)

    def invalid_map(self):
        repo_map = self.root / "repo-map"
        repo_map.mkdir()
        (repo_map / "likec4.config.json").write_text('{"name":"root"}\n', encoding="utf-8")
        (repo_map / "kinds.c4").write_text(
            "specification {\n  element node\n  element repo\n}\n", encoding="utf-8")
        (repo_map / "model.c4").write_text("model { syntax error }\n", encoding="utf-8")
        approvals = repo_map / "approvals.txt"
        approvals.write_text("", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Fixture"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email",
                        "fixture@example.invalid"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "add", "repo-map"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-m", "invalid map fixture"],
                       check=True, capture_output=True)
        quote = Path(self.temporary.name) / "xy-goal" / "quotes" / "987654.md"
        quote.parent.mkdir(parents=True)
        quote.write_text("{}", encoding="utf-8")
        return repo_map, approvals, quote

    @staticmethod
    def run_main_capture(arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_missing_command_and_missing_required_parameters_exit_two(self):
        self.assertEqual(self.run_main(["repo_map.py"]), 2)
        self.assertEqual(self.run_main(["repo_map.py", "init", "--repo", str(self.root)]), 2)

    def test_check_of_a_repository_without_a_map_exits_one(self):
        self.assertEqual(self.run_main(["repo_map.py", "check", "--repo", str(self.root)]), 1)

    def test_init_requires_an_absolute_repository_root(self):
        self.assertEqual(self.run_main(["repo_map.py", "init", "--repo", ".", "--id", "root",
                                        "--title", "Root", "--description", "A repository."]), 2)

    def test_approval_rejects_invalid_syntax_before_printing_a_digest(self):
        self.invalid_map()
        commands = []

        def fail_validation(arguments):
            commands.append(arguments)
            return subprocess.CompletedProcess([], 1, "syntax location", "syntax detail")

        with patch("repo_map_lib.likec4.run_likec4", side_effect=fail_validation):
            result, stdout, stderr = self.run_main_capture(
                ["repo_map.py", "approval", "--repo", str(self.root)])

        self.assertEqual(result, 1)
        self.assertEqual([command[0] for command in commands], ["validate"])
        self.assertNotIn("repo-map sha256", stdout)
        self.assertIn("syntax location", stderr)

    def test_record_rejects_invalid_syntax_without_writing_an_approval(self):
        _, approvals, quote = self.invalid_map()
        commands = []

        def fail_validation(arguments):
            commands.append(arguments)
            return subprocess.CompletedProcess([], 1, "syntax location", "syntax detail")

        with patch("repo_map_lib.likec4.run_likec4", side_effect=fail_validation):
            result, stdout, stderr = self.run_main_capture([
                "repo_map.py", "record", "--repo", str(self.root), "--approval", str(quote)])

        self.assertEqual(result, 1)
        self.assertEqual([command[0] for command in commands], ["validate"])
        self.assertEqual(approvals.read_text(encoding="utf-8"), "")
        self.assertNotIn("已记录改图同意", stdout)
        self.assertIn("syntax location", stderr)


if __name__ == "__main__":
    unittest.main()
