"""The approval command compares against the first attached worktree."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.approval_command import approval_report
from repo_map_lib.structure import structure_digest


def model(child_title, description):
    return {"elements": {
        "root": {"id": "root", "kind": "node", "title": "Root",
                 "description": {"txt": "Whole repository."}, "style": {}},
        "root.child": {"id": "root.child", "kind": "node", "title": child_title,
                       "description": {"txt": description}, "style": {}},
    }, "relations": {}}


def initialize_repository(root, include_map):
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"],
                   check=True, capture_output=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    if include_map:
        repo_map = root / "repo-map"
        repo_map.mkdir()
        (repo_map / "model.c4").write_text("model {}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "fixture"],
                   check=True, capture_output=True)


class ApprovalCommandTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"

    def test_first_map_lists_full_node_details_and_digest_matches_check_and_record(self):
        initialize_repository(self.root, False)
        (self.root / "repo-map").mkdir()
        current = model("Child", "Does one task.")
        with patch("repo_map_lib.approval_command.export_model", return_value=current):
            changes, digest_line = approval_report(self.root)
        self.assertTrue(any("新增节点：root.child" in line and "Does one task." in line
                            for line in changes), changes)
        self.assertEqual(digest_line, f"repo-map sha256 {structure_digest(current)}")

    def test_main_worktree_detached_head_is_rejected(self):
        initialize_repository(self.root, True)
        subprocess.run(["git", "-C", str(self.root), "checkout", "--detach", "HEAD"],
                       check=True, capture_output=True)
        with patch("repo_map_lib.approval_command.export_model", return_value=model("Child", "Text.")):
            with self.assertRaisesRegex(ValueError, "分离头指针"):
                approval_report(self.root)


if __name__ == "__main__":
    unittest.main()
