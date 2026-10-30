"""Command-line argument and exit-code behavior."""
from pathlib import Path
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest

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

    def test_missing_command_and_missing_required_parameters_exit_two(self):
        self.assertEqual(self.run_main(["repo_map.py"]), 2)
        self.assertEqual(self.run_main(["repo_map.py", "init", "--repo", str(self.root)]), 2)

    def test_check_of_a_repository_without_a_map_exits_one(self):
        self.assertEqual(self.run_main(["repo_map.py", "check", "--repo", str(self.root)]), 1)

    def test_init_requires_an_absolute_repository_root(self):
        self.assertEqual(self.run_main(["repo_map.py", "init", "--repo", ".", "--id", "root",
                                        "--title", "Root", "--description", "A repository."]), 2)


if __name__ == "__main__":
    unittest.main()
