#!/usr/bin/env python3
"""Behavior tests for check_file_length.py."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = Path(__file__).with_name("check_file_length.py")


class CheckFileLengthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repo_number = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_repo(self, name: str | None = None) -> Path:
        self.repo_number += 1
        repository = self.base / (name or f"repo-{self.repo_number}")
        repository.mkdir(parents=True)
        self.git(repository, "init", "-q")
        self.git(repository, "config", "user.name", "File Length Test")
        self.git(repository, "config", "user.email", "file-length@example.test")
        return repository

    def git(self, repository: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def write(self, repository: Path, relative: str, content: str | bytes) -> Path:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def lines(self, count: int, final_newline: bool = True) -> str:
        content = "\n".join(f"line {number}" for number in range(count))
        return content + ("\n" if final_newline else "")

    def add(self, repository: Path, *relative_paths: str) -> None:
        self.git(repository, "add", "--", *relative_paths)

    def add_exception(
        self,
        repository: Path,
        name: str,
        reason: str = "A concrete maintenance reason.",
        tracked: bool = True,
    ) -> None:
        self.write(repository, f"file-length-exceptions/{name}", reason)
        if tracked:
            self.add(repository, f"file-length-exceptions/{name}")

    def check(self, root: Path, root_argument: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), "--root", root_argument or str(root)],
            capture_output=True,
            text=True,
        )

    def test_499_lines_pass_without_reminder(self) -> None:
        repo = self.make_repo()
        self.write(repo, "small.py", self.lines(499))
        self.add(repo, "small.py")
        result = self.check(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("到上限", result.stdout)

    def test_500_lines_prints_reminder(self) -> None:
        repo = self.make_repo()
        self.write(repo, "edge.py", self.lines(500))
        self.add(repo, "edge.py")
        result = self.check(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("edge.py: 500 行，到上限", result.stdout)

    def test_501_lines_without_exception_directory_fails(self) -> None:
        repo = self.make_repo()
        self.write(repo, "large.py", self.lines(501))
        self.add(repo, "large.py")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("没有 file-length-exceptions/ 目录", result.stderr)

    def test_file_exception_allows_large_file_without_showing_reason(self) -> None:
        repo = self.make_repo()
        self.write(repo, "large.py", self.lines(501))
        self.add(repo, "large.py")
        self.add_exception(repo, "large-py", "private reason text")
        result = self.check(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("large.py: 501 行，有理由", result.stdout)
        self.assertNotIn("private reason text", result.stdout)

    def test_empty_and_whitespace_reasons_fail(self) -> None:
        for index, reason in enumerate(("", " \n\t ")):
            with self.subTest(reason=repr(reason)):
                repo = self.make_repo(f"empty-{index}")
                self.write(repo, "large.py", self.lines(501))
                self.add(repo, "large.py")
                self.add_exception(repo, "large-py", reason)
                result = self.check(repo)
                self.assertEqual(result.returncode, 1)
                self.assertIn("理由为空", result.stderr)

    def test_exception_directory_cannot_contain_subdirectories(self) -> None:
        repo = self.make_repo()
        self.write(repo, "file-length-exceptions/nested/reason", "reason")
        self.add(repo, "file-length-exceptions/nested/reason")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("不许有子目录", result.stderr)

    def test_unknown_exception_name_fails(self) -> None:
        repo = self.make_repo()
        self.write(repo, "small.py", self.lines(10))
        self.add(repo, "small.py")
        self.add_exception(repo, "missing-path", "reason")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("不对应 Git 跟踪的文件或目录", result.stderr)

    def test_file_exception_for_500_lines_is_expired(self) -> None:
        repo = self.make_repo()
        self.write(repo, "edge.py", self.lines(500))
        self.add(repo, "edge.py")
        self.add_exception(repo, "edge-py")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("文件条目已过期", result.stderr)

    def test_colliding_file_names_fail(self) -> None:
        repo = self.make_repo()
        self.write(repo, "a/b", self.lines(501))
        self.write(repo, "a-b", self.lines(501))
        self.add(repo, "a/b", "a-b")
        self.add_exception(repo, "a-b")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("多个路径映射为同一条目名", result.stderr)

    def test_file_and_directory_name_collision_is_ambiguous(self) -> None:
        repo = self.make_repo()
        self.write(repo, "a/b/child.py", self.lines(501))
        self.write(repo, "a-b", self.lines(501))
        self.add(repo, "a/b/child.py", "a-b")
        self.add_exception(repo, "a-b")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("多个路径映射为同一条目名", result.stderr)
        self.assertIn("条目名有歧义", result.stderr)

    def test_untracked_large_file_is_ignored(self) -> None:
        repo = self.make_repo()
        self.write(repo, "untracked.py", self.lines(501))
        result = self.check(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("untracked.py", result.stdout + result.stderr)

    def test_directory_exception_allows_descendant_file(self) -> None:
        repo = self.make_repo()
        self.write(repo, "modules/large.py", self.lines(501))
        self.add(repo, "modules/large.py")
        self.add_exception(repo, "modules")
        result = self.check(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("modules/large.py: 501 行，有理由", result.stdout)

    def test_directory_exception_for_unknown_directory_fails(self) -> None:
        repo = self.make_repo()
        self.write(repo, "src/small.py", self.lines(10))
        self.add(repo, "src/small.py")
        self.add_exception(repo, "missing-directory")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("不对应 Git 跟踪的文件或目录", result.stderr)

    def test_directory_exception_without_large_descendant_is_expired(self) -> None:
        repo = self.make_repo()
        self.write(repo, "modules/small.py", self.lines(10))
        self.add(repo, "modules/small.py")
        self.add_exception(repo, "modules")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("目录条目已过期", result.stderr)

    def test_csv_file_is_checked(self) -> None:
        repo = self.make_repo()
        self.write(repo, "large.csv", self.lines(501))
        self.add(repo, "large.csv")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("large.csv: 501 行，没有理由文件", result.stderr)

    def test_json_file_can_have_file_exception(self) -> None:
        repo = self.make_repo()
        self.write(repo, "large.json", self.lines(501))
        self.add(repo, "large.json")
        self.add_exception(repo, "large-json")
        result = self.check(repo)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_last_line_without_newline_is_counted(self) -> None:
        repo = self.make_repo()
        self.write(repo, "large.py", self.lines(501, final_newline=False))
        self.add(repo, "large.py")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("large.py: 501 行", result.stderr)

    def test_untracked_exception_file_fails(self) -> None:
        repo = self.make_repo()
        self.write(repo, "large.py", self.lines(501))
        self.add(repo, "large.py")
        self.add_exception(repo, "large-py", tracked=False)
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("条目没有计入 Git", result.stderr)

    def test_symlink_source_is_ignored(self) -> None:
        repo = self.make_repo()
        outside = self.write(self.base, "outside.py", self.lines(501))
        link = repo / "linked.py"
        link.symlink_to(outside)
        self.add(repo, "linked.py")
        result = self.check(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("linked.py: 501 行", result.stdout + result.stderr)

    def test_root_and_nested_repositories_use_their_own_exceptions(self) -> None:
        root = self.make_repo("root")
        self.write(root, "root.py", self.lines(501))
        self.add(root, "root.py")
        self.add_exception(root, "root-py")
        child = self.make_repo("root/child")
        self.write(child, "child.py", self.lines(501))
        self.add(child, "child.py")
        self.add_exception(child, "child-py")
        result = self.check(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("发现仓库：2", result.stdout)
        self.assertIn("child: child.py: 501 行，有理由", result.stdout)

    def test_nested_repository_without_exception_fails(self) -> None:
        root = self.make_repo("root")
        child = self.make_repo("root/child")
        self.write(child, "child.py", self.lines(501))
        self.add(child, "child.py")
        result = self.check(root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("child: child.py: 501 行，没有理由文件", result.stderr)

    def test_root_directory_exception_is_rejected(self) -> None:
        repo = self.make_repo()
        self.add_exception(repo, "-")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("不许根目录条目", result.stderr)

    def test_worktree_is_seen_but_common_repository_is_checked_once(self) -> None:
        repo = self.make_repo()
        self.write(repo, "large.py", self.lines(501))
        self.add(repo, "large.py")
        self.add_exception(repo, "large-py")
        self.git(repo, "commit", "-qm", "add large source and reason")
        worktree = repo / ".worktrees" / "linked"
        self.git(repo, "worktree", "add", "--detach", str(worktree), "HEAD")
        result = self.check(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("发现仓库：1", result.stdout)
        self.assertEqual(result.stdout.count("large.py: 501 行，有理由"), 1)

    def test_nul_binary_file_is_ignored(self) -> None:
        repo = self.make_repo()
        self.write(repo, "binary.dat", b"line\n" * 500 + b"part\0\n")
        self.add(repo, "binary.dat")
        result = self.check(repo)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_non_utf8_file_is_ignored(self) -> None:
        repo = self.make_repo()
        self.write(repo, "binary.dat", b"line\n" * 500 + b"\xff\n")
        self.add(repo, "binary.dat")
        result = self.check(repo)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_root_must_be_absolute(self) -> None:
        repo = self.make_repo()
        result = self.check(repo, ".")
        self.assertEqual(result.returncode, 2)

    def test_root_must_be_repository_top_level(self) -> None:
        repo = self.make_repo()
        child = repo / "nested"
        child.mkdir()
        result = self.check(child)
        self.assertEqual(result.returncode, 2)

    def test_invalid_nested_git_pointer_fails_the_check(self) -> None:
        repo = self.make_repo()
        invalid = repo / "fixture"
        invalid.mkdir()
        (invalid / ".git").write_text("gitdir: missing-target\n", encoding="utf-8")
        result = self.check(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("读 git-common-dir 失败", result.stderr)

    def test_build_artifact_repositories_are_skipped(self) -> None:
        repo = self.make_repo()
        artifact = repo / "target" / "frozen_source_v3"
        artifact.mkdir(parents=True)
        self.git(artifact, "init", "-q")
        result = self.check(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("发现仓库：1", result.stdout)

    def test_tmp_and_output_repositories_are_discovered(self) -> None:
        root = self.make_repo("root")
        self.make_repo("root/tmp/nested")
        self.make_repo("root/output-report")
        result = self.check(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("发现仓库：3", result.stdout)


if __name__ == "__main__":
    unittest.main()
