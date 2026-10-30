"""LikeC4 command wrapper validation and export ordering tests."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.likec4 import export_model


class LikeC4Test(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo_map = Path(self.temporary.name) / "repo-map"
        self.repo_map.mkdir()

    def test_export_validates_before_exporting(self):
        commands = []

        def run(arguments):
            commands.append(arguments)
            if arguments[0] == "validate":
                return subprocess.CompletedProcess([], 0, "valid", "")
            output = Path(arguments[arguments.index("-o") + 1])
            output.write_text('{"elements":{},"relations":{}}', encoding="utf-8")
            return subprocess.CompletedProcess([], 0, "exported", "")

        with patch("repo_map_lib.likec4.run_likec4", side_effect=run):
            model = export_model(self.repo_map)

        self.assertEqual([arguments[0] for arguments in commands], ["validate", "export"])
        self.assertEqual(model, {"elements": {}, "relations": {}})

    def test_invalid_syntax_stops_before_export_and_preserves_validate_output(self):
        commands = []

        def run(arguments):
            commands.append(arguments)
            if arguments[0] != "validate":
                self.fail("export must not run after validation fails")
            return subprocess.CompletedProcess([], 1, "syntax location", "syntax detail")

        with patch("repo_map_lib.likec4.run_likec4", side_effect=run):
            with self.assertRaises(ValueError) as raised:
                export_model(self.repo_map)

        self.assertIn("syntax location", str(raised.exception))
        self.assertIn("syntax detail", str(raised.exception))
        self.assertEqual([arguments[0] for arguments in commands], ["validate"])


if __name__ == "__main__":
    unittest.main()
