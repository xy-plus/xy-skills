import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

from cleanup_test_support import stop_process


SCRIPT = Path(__file__).resolve().parents[1] / "clean.py"


class EarlyDeleteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name)
        self.root = self.temporary / "runs"
        self.root.mkdir()
        self.project = self.root / "-home-xy-project"
        self.item = self.project / "20261120-110000-task"
        self.item.mkdir(parents=True)
        self.target = self.item / "obsolete-build"
        self.target.mkdir()
        (self.target / "output.txt").write_text("作废产物", encoding="utf-8")
        self.home = self.temporary / "home"
        self.units = self.home / ".config" / "systemd" / "user"
        self.units.mkdir(parents=True)
        settings_directory = self.home / ".claude"
        settings_directory.mkdir()
        (settings_directory / "settings.json").write_text(
            json.dumps({"env": {"RUNS_ROOT": str(self.root)}}), encoding="utf-8")

    def run_delete(self, target, *extra, confirmed=True):
        arguments = [sys.executable, "-B", str(SCRIPT), "delete-obsolete",
                     "--target", str(target)]
        if confirmed:
            arguments.append("--other-sessions-checked")
        arguments.extend(extra)
        environment = os.environ.copy()
        environment["HOME"] = str(self.home)
        return subprocess.run(arguments, capture_output=True, text=True, check=False, env=environment)

    def assert_rejected_unchanged(self, target, *extra, confirmed=True):
        before = self.snapshot()
        result = self.run_delete(target, *extra, confirmed=confirmed)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)
        return result

    def snapshot(self):
        return {path.relative_to(self.temporary):
                path.read_bytes() if path.is_file() and not path.is_symlink() else
                ("symlink", os.readlink(path)) if path.is_symlink() else None
                for path in self.temporary.rglob("*")}

    def test_delete_removes_only_the_named_item_inside_runs(self):
        sibling = self.item / "keep.txt"
        sibling.write_text("保留", encoding="utf-8")
        result = self.run_delete(self.target)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertFalse(self.target.exists())
        self.assertEqual(sibling.read_text(encoding="utf-8"), "保留")
        self.assertTrue(self.item.is_dir())
        self.assertTrue(self.root.is_dir())

    def test_delete_refuses_missing_confirmation_without_touching_target(self):
        result = self.assert_rejected_unchanged(self.target, confirmed=False)
        self.assertIn("--other-sessions-checked", result.stderr)

    def test_delete_rejects_root_project_inbox_outside_and_nonliteral_targets(self):
        inbox = self.project / "xy-goal-inbox"
        session_inbox = inbox / "session-1"
        session_inbox.mkdir(parents=True)
        (session_inbox / "1.md").write_text("原话", encoding="utf-8")
        outside = self.temporary / "outside.txt"
        outside.write_text("外部数据", encoding="utf-8")
        for label, target in (
            ("runs root", self.root),
            ("project directory", self.project),
            ("whole inbox", inbox),
            ("inbox session", session_inbox),
            ("outside root", outside),
            ("relative", Path("relative/path")),
            ("nonliteral", self.item / ".." / "20261120-110000-task" / "obsolete-build"),
        ):
            with self.subTest(label=label):
                result = self.assert_rejected_unchanged(target)
                self.assertNotEqual(result.stderr, "")

    def test_delete_rejects_symlinked_parents_and_outside_symlink_targets(self):
        outside_project = self.temporary / "outside-project"
        outside_item = outside_project / "20261120-110000-task"
        outside_item.mkdir(parents=True)
        outside_data = outside_item / "data"
        outside_data.write_text("外部数据", encoding="utf-8")
        linked_project = self.root / "-linked-project"
        linked_project.symlink_to(outside_project, target_is_directory=True)
        parent_link_target = linked_project / outside_item.name / outside_data.name
        self.assert_rejected_unchanged(parent_link_target)

        outside_link = self.item / "outside-link"
        outside_link.symlink_to(outside_data)
        result = self.assert_rejected_unchanged(outside_link)
        self.assertIn("软链", result.stderr)
        self.assertEqual(outside_data.read_text(encoding="utf-8"), "外部数据")

    def test_delete_rejects_process_systemd_and_runner_task_references(self):
        process_code = "import os, sys; os.chdir(os.environ['DELETE_TEST_DIRECTORY']); print('ready', flush=True); sys.stdin.read()"
        environment = os.environ.copy()
        environment["DELETE_TEST_DIRECTORY"] = str(self.target)
        process = subprocess.Popen([sys.executable, "-c", process_code], env=environment,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        self.addCleanup(stop_process, process)
        ready, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(ready)
        self.assertEqual(process.stdout.readline(), "ready\n")
        result = self.assert_rejected_unchanged(self.target)
        self.assertIn("进程", result.stderr)
        stop_process(process)

        unit = self.units / "uses-target.service"
        unit.write_text(f"ExecStart=/bin/tool {self.target}\n", encoding="utf-8")
        result = self.assert_rejected_unchanged(self.target)
        self.assertIn("systemd", result.stderr)
        unit.unlink()

        task_directory = self.item / "sub-agent-runner" / "tasks"
        task_directory.mkdir(parents=True)
        (task_directory / "still-running.json").write_text(
            json.dumps({"task": "still-running", "dir": str(self.target / "working")}),
            encoding="utf-8",
        )
        result = self.assert_rejected_unchanged(self.target)
        self.assertIn("sub-agent-runner", result.stderr)

    def test_delete_unlinks_in_root_symlink_without_touching_its_target(self):
        data = self.item / "real-data"
        data.mkdir()
        (data / "keep.txt").write_text("保留", encoding="utf-8")
        link = self.item / "linked-data"
        link.symlink_to(data, target_is_directory=True)
        result = self.run_delete(link)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(link.is_symlink())
        self.assertEqual((data / "keep.txt").read_text(encoding="utf-8"), "保留")

if __name__ == "__main__":
    unittest.main()
