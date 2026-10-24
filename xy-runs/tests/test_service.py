"""Execute the service's launcher with a temporary HOME and an argv receiver."""

import configparser
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

import clean

SERVICE = Path(__file__).resolve().parents[1] / "systemd" / "runs-cleaner.service"


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home with spaces"
        (self.home / ".claude").mkdir(parents=True)
        repository = self.home / "xy-skills" / "xy-runs"
        repository.mkdir(parents=True)
        # The boundary receiver only reports argv; it never scans the real roots.
        (repository / "clean.py").write_text(
            "import json, sys\nprint(json.dumps(sys.argv[1:]))\n"
        )
        self.settings = self.home / ".claude" / "settings.json"

    def launch(self):
        self.assertTrue(SERVICE.is_file(), "the systemd service is missing")
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(SERVICE)
        command = shlex.split(unit["Service"]["ExecStart"])
        command = [argument.replace("%h", str(self.home)) for argument in command]
        environment = os.environ.copy()
        environment.update(HOME=str(self.home), RUNS_ROOT="/must-not-use-environment")
        return subprocess.run(command, env=environment, text=True, capture_output=True,
                              check=False, cwd=self.home)

    # The launcher must read settings at each invocation and preserve spaced paths.
    def test_service_reads_settings_each_time_and_starts_in_list_mode(self):
        for name in ("first runs", "second runs"):
            with self.subTest(name=name):
                runs = self.home / name
                self.settings.write_text(json.dumps({"env": {"RUNS_ROOT": str(runs)}}))
                result = self.launch()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), [
                    "--mode", "list",
                    "/data/user/xy/agent-scratch", "/data/user/xy/evidence",
                    str(self.home / ".claude" / "xy-workflow"),
                    str(self.home / ".codex-subagent*" / "sessions" / "*" / "*"),
                    str(runs), str(runs / "-*"), str(runs / "-*" / "xy-goal-inbox"),
                ])

    # An unset setting must not fall back to the shell's RUNS_ROOT or home directory.
    def test_missing_runs_setting_fails_before_cleaner_starts(self):
        self.settings.write_text('{"env": {}}')
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("RUNS_ROOT", result.stderr)

    # An empty root must not become a relative path or a filesystem-root glob.
    def test_empty_runs_setting_fails_before_cleaner_starts(self):
        self.settings.write_text('{"env": {"RUNS_ROOT": ""}}')
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("RUNS_ROOT", result.stderr)

    # The configured root is a literal path; only the added project levels are globs.
    def test_literal_glob_characters_do_not_select_a_sibling_root(self):
        runs = self.home / "run[1]"
        project = runs / "-project"
        inbox = project / "xy-goal-inbox"
        inbox.mkdir(parents=True)
        sibling = self.home / "run1"
        (sibling / "-other-project").mkdir(parents=True)
        self.settings.write_text(json.dumps({"env": {"RUNS_ROOT": str(runs)}}))
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        patterns = json.loads(result.stdout)[-3:]
        errors = io.StringIO()
        roots = clean.expand_roots(patterns, errors)
        self.assertEqual(set(roots), {runs, project, inbox})
        self.assertEqual(errors.getvalue(), "")

    # A relative value must fail before the cleaner is started in any working directory.
    def test_relative_runs_setting_fails_before_cleaner_starts(self):
        self.settings.write_text('{"env": {"RUNS_ROOT": "runs"}}')
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("RUNS_ROOT", result.stderr)


if __name__ == "__main__":
    unittest.main()
