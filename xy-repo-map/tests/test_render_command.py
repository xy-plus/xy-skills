"""Render mode, output-directory, tree-check, and draft marker tests."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.render_command import render_repository
from repo_map_lib.ownership import Owner


def element(element_id, kind, links):
    value = {"id": element_id, "kind": kind, "title": element_id,
             "description": {"txt": "一句功能描述。"}, "style": {}}
    if links is not None:
        value["links"] = links
    return value


class RenderCommandTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "parent"
        self.child = self.root / "vendor"
        for repository in (self.root, self.child):
            repository.mkdir(parents=True)
            subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
            map_root = repository / "repo-map"
            map_root.mkdir()
            (map_root / "model.c4").write_text("model {}\n", encoding="utf-8")
        self.parent_model = {"elements": {
            "workspace": element("workspace", "node", []),
            "workspace.engine": element("workspace.engine", "repo", [
                {"url": "../vendor/", "title": "repo"}]),
        }, "relations": {}}
        self.child_model = {"elements": {
            "engine": element("engine", "node", []),
            "engine.cache": element("engine.cache", "node", []),
        }, "relations": {}}
        self.output = Path(self.temporary.name) / "site"

    def fake_check(self, repository, check_approval):
        if Path(repository).resolve() == self.root.resolve():
            return [], self.parent_model, {}, []
        if check_approval:
            return ["图未经同意：repo-map sha256 " + "a" * 64], self.child_model, {}, []
        return [], self.child_model, {}, []

    def test_formal_render_rejects_an_unapproved_child_before_build(self):
        with patch("repo_map_lib.render_command.check_repository", side_effect=self.fake_check):
            problems = render_repository(self.root, "正式", self.output)
        self.assertTrue(any("vendor" in problem and "未经同意" in problem for problem in problems), problems)
        self.assertFalse(self.output.exists())

    def test_render_fails_with_the_path_when_an_owned_file_cannot_be_read(self):
        source = self.root / "src" / "unreadable.py"
        source.parent.mkdir()
        source.write_text("def run():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "src/unreadable.py"],
                       check=True, capture_output=True)
        self.parent_model["elements"]["workspace"]["links"] = [
            {"url": "../src/unreadable.py", "title": "code"},
        ]
        original_read_bytes = Path.read_bytes

        def fail_for_owned_file(path):
            if path == source:
                raise PermissionError("fixture denies reading")
            return original_read_bytes(path)

        def checked_with_owned_file(repository, check_approval):
            if Path(repository).resolve() == self.root.resolve():
                return [], self.parent_model, {
                    "src/unreadable.py": Owner("workspace", "code")
                }, ["src/unreadable.py"]
            return self.fake_check(repository, check_approval)

        with patch("repo_map_lib.render_command.check_repository",
                   side_effect=checked_with_owned_file), \
                patch.object(Path, "read_bytes", fail_for_owned_file):
            problems = render_repository(self.root, "草稿", self.output)

        self.assertTrue(any("无法读取" in problem and str(source) in problem
                            for problem in problems), problems)
        self.assertFalse(self.output.exists())

    def test_render_rejects_duplicate_root_ids_across_the_repository_tree(self):
        duplicate_child = {"elements": {
            "workspace": element("workspace", "node", []),
            "workspace.cache": element("workspace.cache", "node", []),
        }, "relations": {}}

        def duplicate_check(repository, check_approval):
            model = self.parent_model if Path(repository).resolve() == self.root.resolve() else duplicate_child
            return [], model, {}, []

        with patch("repo_map_lib.render_command.check_repository", side_effect=duplicate_check):
            problems = render_repository(self.root, "草稿", self.output)
        self.assertTrue(any("根 id 重复" in problem and "workspace" in problem
                            for problem in problems), problems)
        self.assertFalse(self.output.exists())

    def test_render_refuses_nonempty_output_directory(self):
        self.output.mkdir()
        (self.output / "keep.txt").write_text("keep", encoding="utf-8")
        problems = render_repository(self.root, "草稿", self.output)
        self.assertTrue(any("不存在或为空" in problem for problem in problems), problems)
        self.assertEqual((self.output / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_failed_write_preserves_an_existing_empty_output_directory(self):
        self.output.mkdir()
        with patch("repo_map_lib.render_command.check_repository", side_effect=self.fake_check), \
                patch("repo_map_lib.render_command.write_site", side_effect=OSError("fixture failure")):
            problems = render_repository(self.root, "草稿", self.output)
        self.assertTrue(any("fixture failure" in problem for problem in problems), problems)
        self.assertTrue(self.output.is_dir())
        self.assertEqual(list(self.output.iterdir()), [])

    def test_render_refuses_output_inside_any_repository_without_writing(self):
        destinations = (self.root, self.root / "generated-site", self.child / "generated-site")
        for destination in destinations:
            with self.subTest(destination=destination):
                with patch("repo_map_lib.render_command.check_repository",
                           side_effect=self.fake_check):
                    problems = render_repository(self.root, "草稿", destination)
                self.assertTrue(any("仓内" in problem for problem in problems), problems)
                self.assertFalse((destination / "index.html").exists())


if __name__ == "__main__":
    unittest.main()
