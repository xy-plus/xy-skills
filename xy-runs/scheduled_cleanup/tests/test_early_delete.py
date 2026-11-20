import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


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
        settings_directory = self.home / ".claude"
        settings_directory.mkdir(parents=True)
        (settings_directory / "settings.json").write_text(
            json.dumps({"env": {"RUNS_ROOT": str(self.root)}}), encoding="utf-8")
    def run_delete(self, target):
        environment = os.environ.copy()
        environment["HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "delete-obsolete", "--target", str(target)],
            capture_output=True, text=True, check=False, env=environment,
        )

    def assert_rejected_unchanged(self, target):
        before = self.snapshot()
        result = self.run_delete(target)
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
