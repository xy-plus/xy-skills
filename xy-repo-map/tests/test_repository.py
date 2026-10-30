"""Git-root discovery and repository-map initialization tests."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map import main
from repo_map_lib.git_repository import (discover_subrepositories, model_paths,
                                         require_repository_root)
from repo_map_lib.initializer import init_map


def run_git(path, *arguments):
    return subprocess.run(["git", "-C", str(path), *arguments], check=True,
                          capture_output=True, text=True).stdout.strip()


def initialize_git(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "workspace"
        initialize_git(self.root)

    def test_repository_argument_must_be_absolute_git_root(self):
        self.assertEqual(require_repository_root(self.root), self.root.resolve())
        with self.assertRaisesRegex(ValueError, "绝对路径"):
            require_repository_root(Path("."))
        child = self.root / "nested"
        child.mkdir()
        with self.assertRaisesRegex(ValueError, "仓根"):
            require_repository_root(child)

    def test_init_writes_explicit_root_and_discovered_repo_placeholders(self):
        child = self.root / "vendor"
        initialize_git(child)
        (self.root / ".gitignore").write_text("vendor/\n", encoding="utf-8")

        init_map(self.root, "xy_skills", "xy-skills", "一组帮助维护代码仓库的 skill。")

        map_root = self.root / "repo-map"
        self.assertEqual(json.loads((map_root / "likec4.config.json").read_text())[
            "name"], "xy_skills")
        self.assertEqual((map_root / "kinds.c4").read_text(encoding="utf-8"),
                         "specification {\n  element node\n  element repo\n}\n")
        self.assertEqual((map_root / "approvals.txt").read_text(encoding="utf-8"), "")
        model_text = (map_root / "model.c4").read_text(encoding="utf-8")
        self.assertIn("xy_skills = node 'xy-skills'", model_text)
        self.assertIn("vendor = repo 'vendor'", model_text)
        self.assertIn("link ../vendor/ 'repo'", model_text)

    def test_init_refuses_to_replace_an_existing_map(self):
        init_map(self.root, "root", "Root", "一句功能描述。")
        with self.assertRaisesRegex(ValueError, "已经存在"):
            init_map(self.root, "root", "Root", "一句功能描述。")

    def test_model_paths_only_excludes_the_standard_kinds_file(self):
        map_root = self.root / "repo-map"
        nested = map_root / "parts"
        nested.mkdir(parents=True)
        (map_root / "kinds.c4").write_text("standard\n", encoding="utf-8")
        (map_root / "model.c4").write_text("model {}\n", encoding="utf-8")
        (nested / "kinds.c4").write_text("model {}\n", encoding="utf-8")

        found = [path.relative_to(map_root).as_posix() for path in model_paths(map_root)]

        self.assertEqual(found, ["model.c4", "parts/kinds.c4"])

    def test_ignored_nested_git_repository_is_discovered_but_worktree_is_not(self):
        child = self.root / "embedded"
        initialize_git(child)
        (self.root / ".gitignore").write_text("embedded/\n.worktrees/\n", encoding="utf-8")
        found = discover_subrepositories(self.root)
        self.assertEqual(found, ["embedded"])

        run_git(self.root, "config", "user.name", "Fixture")
        run_git(self.root, "config", "user.email", "fixture@example.invalid")
        (self.root / "tracked.txt").write_text("base\n", encoding="utf-8")
        run_git(self.root, "add", ".")
        run_git(self.root, "commit", "-m", "fixture")
        worktree = self.root / ".worktrees" / "branch"
        worktree.parent.mkdir()
        subprocess.run(["git", "-C", str(self.root), "worktree", "add", "--detach",
                        str(worktree), "HEAD"], check=True, capture_output=True)
        self.assertEqual(discover_subrepositories(self.root), ["embedded"])

    def test_untracked_directory_is_searched_but_ignored_directory_is_not_recursed(self):
        container = self.root / "container"
        nested = container / "nested"
        ignored_nested = container / "ignored" / "hidden"
        initialize_git(nested)
        initialize_git(ignored_nested)
        (self.root / ".gitignore").write_text("container/ignored/\n", encoding="utf-8")

        self.assertEqual(discover_subrepositories(self.root), ["container/nested"])

    def test_ignored_nested_repository_is_checked_at_its_own_root(self):
        vendor = self.root / "vendor"
        vendor.mkdir()
        (vendor / "tracked.txt").write_text("tracked parent file\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("vendor/child/\n", encoding="utf-8")
        run_git(self.root, "add", ".gitignore", "vendor/tracked.txt")
        initialize_git(vendor / "child")

        self.assertEqual(discover_subrepositories(self.root), ["vendor/child"])

    def test_untracked_directory_scan_error_names_the_directory(self):
        init_map(self.root, "root", "Root", "A repository root.")
        container = self.root / "container"
        nested = container / "nested"
        nested.mkdir(parents=True)
        (nested / "file.txt").write_text("untracked\n", encoding="utf-8")
        original_scandir = os.scandir

        def fail_for_container(path):
            if Path(path) == container:
                raise PermissionError("fixture denies listing")
            return original_scandir(path)

        exported_model = {"elements": {
            "root": {"id": "root", "kind": "node", "title": "Root",
                     "description": {"txt": "A repository root."}, "links": []},
        }, "relations": {}}
        stderr = io.StringIO()
        with patch("repo_map_lib.checker.validate_likec4",
                   return_value=subprocess.CompletedProcess([], 0, "", "")), \
                patch("repo_map_lib.checker.export_model", return_value=exported_model), \
                patch("repo_map_lib.git_repository.os.scandir", side_effect=fail_for_container), \
                contextlib.redirect_stderr(stderr):
            result = main(["repo_map.py", "check", "--repo", str(self.root)])

        self.assertEqual(result, 1)
        self.assertIn("无法读取未跟踪目录", stderr.getvalue())
        self.assertIn(str(container), stderr.getvalue())

    def test_indexed_gitlink_is_discovered_as_a_subrepository(self):
        child = self.root.parent / "separate-child"
        initialize_git(child)
        run_git(child, "config", "user.name", "Fixture")
        run_git(child, "config", "user.email", "fixture@example.invalid")
        (child / "readme.txt").write_text("child\n", encoding="utf-8")
        run_git(child, "add", ".")
        run_git(child, "commit", "-m", "child")
        child_commit = run_git(child, "rev-parse", "HEAD")
        (self.root / "linked").mkdir()
        subprocess.run(["git", "-C", str(self.root), "update-index", "--add", "--cacheinfo",
                        f"160000,{child_commit},linked"], check=True, capture_output=True)
        self.assertEqual(discover_subrepositories(self.root), ["linked"])


if __name__ == "__main__":
    unittest.main()
