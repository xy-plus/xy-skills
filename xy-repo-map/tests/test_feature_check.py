"""Check feature-list rules through the public repository command."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from map_fixture import build_map, feature


ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "xy-repo-map" / "repo_map.py"


class FeatureCheckTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "sample-repo"
        self.repo.mkdir()
        self.write("README.md", "仓库说明。\n")
        self.write("repo-map/features.json", json.dumps(feature("root", "", subrepositories=[], external_dependencies=[]), ensure_ascii=False))
        self.write("repo-map/skip.json", "[]\n")
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, content):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def set_features(self, root):
        self.write("repo-map/features.json", json.dumps(root, ensure_ascii=False))
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)

    def check(self):
        return subprocess.run(
            ["python3", "-B", str(COMMAND), "check", "--repo", str(self.repo)],
            capture_output=True, text=True, check=False,
        )

    def make_nested_repository(self, relative):
        nested = self.repo / relative
        nested.mkdir(parents=True)
        subprocess.run(["git", "-C", str(nested), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(nested), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(nested), "config", "user.email", "fixture@example.invalid"], check=True)
        (nested / "README.md").write_text("nested\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(nested), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(nested), "commit", "-qm", "fixture"], check=True)

    def test_allows_subrepositories_only_on_the_root_feature(self):
        child = feature("child", None, subrepositories=[], external_dependencies=[])
        root = feature("root", "", children=[child], subrepositories=[], external_dependencies=[])
        root["legacy"] = "旧字段"
        self.set_features(root)

        result = self.check()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1)
        self.assertIn("子功能有多余键 subrepositories", output)
        self.assertIn("子功能有多余键 external_dependencies", output)
        self.assertNotIn("根功能有多余键 subrepositories", output)
        self.assertNotIn("根功能有多余键 external_dependencies", output)
        self.assertIn("根功能有多余键 legacy", output)

    def test_rejects_invalid_subrepository_value_shapes_and_paths(self):
        cases = [
            ("不是数组", "sub", "subrepositories 必须是字符串数组"),
            ("元素不是字符串", [1], "subrepositories 必须是字符串数组"),
            ("空路径", [""], "subrepositories 必须是安全的仓内相对目录路径"),
            ("绝对路径", ["/abs"], "subrepositories 必须是安全的仓内相对目录路径"),
            ("结尾斜线", ["sub/"], "subrepositories 必须是安全的仓内相对目录路径"),
            ("上级路径", ["../sub"], "subrepositories 必须是安全的仓内相对目录路径"),
            ("反斜线", ["a\\b"], "subrepositories 必须是安全的仓内相对目录路径"),
            ("NUL", ["sub\x00repo"], "subrepositories 必须是安全的仓内相对目录路径"),
        ]
        for name, subrepositories, expected in cases:
            with self.subTest(value=name):
                root = feature("root", "", subrepositories=subrepositories, external_dependencies=[])
                self.set_features(root)

                result = self.check()
                output = result.stdout + result.stderr

                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, output)

    def test_rejects_unregistered_ignored_nested_repositories_and_lists_each_path(self):
        self.write(".gitignore", "/sub-one/\n/sub-two/\n")
        self.make_nested_repository("sub-one")
        self.make_nested_repository("sub-two")

        result = self.check()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1)
        self.assertIn("sub-one", output)
        self.assertIn("sub-two", output)
        self.assertIn("subrepositories", output)

    def test_accepts_registered_ignored_nested_repository_without_adding_its_files_to_map(self):
        self.write(".gitignore", "/sub/\n")
        self.make_nested_repository("sub")
        self.set_features(feature("root", "", subrepositories=["sub"], external_dependencies=[]))

        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        data = build_map(self, self.repo)
        files = [
            path
            for feature_data in data["features"].values()
            for group in feature_data["files"].values()
            for path in group
        ]
        self.assertFalse(any(path.startswith("sub/") for path in files))

    def test_accepts_a_registered_path_missing_from_the_checkout(self):
        self.set_features(feature("root", "", subrepositories=["quant-deep"], external_dependencies=[]))

        result = self.check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_invalid_external_dependency_names(self):
        cases = [
            ("不是数组", "Claude Code", "external_dependencies 必须是字符串数组"),
            ("元素不是字符串", ["Claude Code", 1], "external_dependencies 必须是字符串数组"),
            ("空名字", [""], "external_dependencies 名字必须是非空字符串、首尾无空白且不含换行"),
            ("空白名字", ["  "], "external_dependencies 名字必须是非空字符串、首尾无空白且不含换行"),
            ("前置空白", [" Claude Code"], "external_dependencies 名字必须是非空字符串、首尾无空白且不含换行"),
            ("尾随空白", ["Claude Code "], "external_dependencies 名字必须是非空字符串、首尾无空白且不含换行"),
            ("换行", ["Claude\nCode"], "external_dependencies 名字必须是非空字符串、首尾无空白且不含换行"),
        ]
        for name, external_dependencies, expected in cases:
            with self.subTest(value=name):
                self.set_features(feature(
                    "root", "", subrepositories=[],
                    external_dependencies=external_dependencies,
                ))

                result = self.check()
                output = result.stdout + result.stderr

                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, output)

    def test_reports_all_external_dependency_content_errors_together(self):
        self.write("README.md", "No external tools are mentioned here.\n")
        self.write("pkg/source.py", "value = 1\n")
        root = feature(
            "root", "", subrepositories=[], external_dependencies=["Unused", "pkg"],
            children=[feature("package", "pkg")],
        )
        self.set_features(root)

        result = self.check()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1)
        self.assertIn("外部依赖没有被任何代码或文档提到：Unused", output)
        self.assertIn("外部依赖不能与仓内目录功能同名：pkg", output)

    def test_reports_registered_paths_that_are_not_directly_ignored_repositories(self):
        self.write("tracked/file.py", "value = 1\n")
        self.write(".gitignore", "/ordinary/\n")
        self.write("ordinary/file.py", "ignored data\n")
        self.set_features(feature(
            "root", "", subrepositories=["tracked", "ordinary", "visible"], external_dependencies=[]
        ))
        self.make_nested_repository("visible")

        result = self.check()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1)
        self.assertIn("登记的子仓不是被忽略规则直接点名的嵌套 Git 仓", output)
        for path in ("tracked", "ordinary", "visible"):
            with self.subTest(path=path):
                self.assertIn(path, output)
        self.assertIn("还不支持子仓", output)

    def test_registered_gitlink_is_still_unsupported_and_mismatched(self):
        child = self.repo.parent / "child-repository"
        subprocess.run(["git", "-C", str(self.repo.parent), "init", str(child), "-q"], check=True)
        subprocess.run(["git", "-C", str(child), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(child), "config", "user.email", "fixture@example.invalid"], check=True)
        (child / "README.md").write_text("child\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(child), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(child), "commit", "-qm", "fixture"], check=True)
        self.set_features(feature("root", "", subrepositories=["linked"], external_dependencies=[]))
        commit = subprocess.run(["git", "-C", str(child), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(
            ["git", "-C", str(self.repo), "update-index", "--add", "--cacheinfo",
             f"160000,{commit},linked"],
            check=True,
        )
        self.make_nested_repository("linked")

        result = self.check()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1)
        self.assertIn("还不支持子仓：linked", output)
        self.assertIn("登记的子仓不是被忽略规则直接点名的嵌套 Git 仓：linked", output)

    def test_rejects_duplicate_json_keys_in_feature_list(self):
        self.write(
            "repo-map/features.json",
            '{"id":"root","id":"other","title":"示例","what":"说明",'
            '"why":{"text":"理由","source":"起草","quote":"","from":""},'
            '"directory":"","subrepositories":[],"external_dependencies":[],'
            '"entries":[],"children":[]}',
        )

        result = self.check()

        self.assertEqual(result.returncode, 1)
        self.assertIn("无法读取 repo-map/features.json", result.stdout + result.stderr)
        self.assertIn("重复键：id", result.stdout + result.stderr)

    def test_rejects_a_missing_feature_key(self):
        root = feature("root", "", subrepositories=[], external_dependencies=[])
        del root["what"]
        del root["subrepositories"]
        del root["external_dependencies"]
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        output = result.stdout + result.stderr
        for key in ("what", "subrepositories", "external_dependencies"):
            with self.subTest(key=key):
                self.assertIn(key, output)

    def test_rejects_an_invalid_identifier_and_duplicate_identifier(self):
        root = feature("Bad_ID", "", subrepositories=[], external_dependencies=[])
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("id", result.stdout + result.stderr)
        root = feature("root", "", subrepositories=[], external_dependencies=[], children=[feature("same", "pkg"), feature("same", "other")])
        self.write("pkg/item.py", "value = 1\n")
        self.write("other/item.py", "value = 2\n")
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("same", result.stdout + result.stderr)

    def test_accepts_only_valid_why_source_combinations(self):
        for source in ("原话", "确认", "起草"):
            with self.subTest(source=source):
                self.set_features(feature("root", "", subrepositories=[], external_dependencies=[], source=source))
                result = self.check()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "功能清单检查通过\n")

    def test_rejects_invalid_why_source_combinations(self):
        invalid = [
            ("原话", "", "契约"),
            ("原话", "逐字引文", ""),
            ("确认", "不允许的引文", "本次回答"),
            ("确认", "", ""),
            ("起草", "不允许的引文", ""),
            ("未知", "", ""),
        ]
        for source, quote, origin in invalid:
            with self.subTest(source=source, quote=quote, origin=origin):
                root = feature("root", "", subrepositories=[], external_dependencies=[])
                root["why"].update(source=source, quote=quote, **{"from": origin})
                self.set_features(root)
                result = self.check()
                self.assertEqual(result.returncode, 1)
                self.assertIn("why", result.stdout + result.stderr)

    def test_requires_the_root_to_cover_the_repository(self):
        self.set_features(feature("root", "pkg", subrepositories=[], external_dependencies=[]))
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("根", result.stdout + result.stderr)

    def test_rejects_missing_duplicate_and_outside_directories(self):
        cases = [
            (feature("root", "", subrepositories=[], external_dependencies=[], children=[feature("missing", "absent")]), "absent"),
            (feature("root", "", subrepositories=[], external_dependencies=[], children=[feature("first", "pkg"), feature("second", "pkg")]), "pkg"),
            (feature("root", "", subrepositories=[], external_dependencies=[], children=[feature("outer", "pkg", [feature("outside", "other")])]), "other"),
        ]
        self.write("pkg/item.py", "value = 1\n")
        self.write("other/item.py", "value = 2\n")
        for root, expected in cases:
            with self.subTest(directory=expected):
                self.set_features(root)
                result = self.check()
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stdout + result.stderr)

    def test_rejects_missing_and_cross_feature_entries(self):
        self.write("pkg/owned.py", "value = 1\n")
        self.write("other/owned.py", "value = 2\n")
        cases = [
            (feature("root", "", subrepositories=[], external_dependencies=[], children=[feature("pkg", "pkg", entries=["pkg/missing.py"])]), "missing.py"),
            (feature("root", "", subrepositories=[], external_dependencies=[], children=[feature("pkg", "pkg", entries=["other/owned.py"])]), "other/owned.py"),
            (feature("root", "", subrepositories=[], external_dependencies=[], children=[feature("pkg", "pkg", [feature("inner", None)], ["other/owned.py"]), feature("other", "other")]), "other/owned.py"),
        ]
        for root, expected in cases:
            with self.subTest(entry=expected):
                self.set_features(root)
                result = self.check()
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stdout + result.stderr)

    def test_rejects_entries_on_a_leaf_directory_feature(self):
        self.write("pkg/owned.py", "value = 1\n")
        self.set_features(feature("root", "", subrepositories=[], external_dependencies=[], children=[feature("pkg", "pkg", entries=["pkg/owned.py"])]))
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("entries", result.stdout + result.stderr)

    def test_rejects_a_nested_git_repository(self):
        nested = self.repo / "nested"
        nested.mkdir()
        subprocess.run(["git", "-C", str(nested), "init", "-q"], check=True)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("子仓", result.stdout + result.stderr)

    def test_rejects_a_gitlink_child_repository(self):
        child = self.repo.parent / "child-repository"
        subprocess.run(["git", "-C", str(self.repo.parent), "init", str(child), "-q"], check=True)
        subprocess.run(["git", "-C", str(child), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(child), "config", "user.email", "fixture@example.invalid"], check=True)
        (child / "README.md").write_text("child\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(child), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(child), "commit", "-qm", "fixture"], check=True)
        commit = subprocess.run(["git", "-C", str(child), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(
            ["git", "-C", str(self.repo), "update-index", "--add", "--cacheinfo",
             f"160000,{commit},linked"],
            check=True,
        )
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("linked", result.stdout + result.stderr)

    def test_rejects_a_known_but_unsupported_code_suffix(self):
        suffixes = (".go", ".java", ".kt", ".swift", ".cpp", ".cc", ".hpp",
                    ".cs", ".rb", ".php", ".scala", ".lua", ".ts", ".css")
        for suffix in suffixes:
            self.write(f"src/legacy{suffix}", "unsupported source\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "src"], check=True)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                self.assertIn(f"legacy{suffix}", result.stdout + result.stderr)

    def test_bad_arguments_exit_with_two(self):
        result = subprocess.run(
            ["python3", "-B", str(COMMAND), "check", "--repo", str(self.repo / "missing")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)

    def _assert_repository_error_for_check_and_render(self, repository, message):
        with tempfile.TemporaryDirectory() as temporary:
            for command in ("check", "render"):
                arguments = ["python3", "-B", str(COMMAND), command, "--repo", str(repository)]
                if command == "render":
                    arguments.extend(["--out", str(Path(temporary) / "rendered")])
                result = subprocess.run(
                    arguments,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                with self.subTest(command=command):
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(message, result.stderr)

    def test_relative_repository_path_exits_with_two(self):
        self._assert_repository_error_for_check_and_render(
            "relative/path",
            "--repo 必须是绝对路径",
        )

    def test_non_root_repository_path_exits_with_two(self):
        self._assert_repository_error_for_check_and_render(
            self.repo / "repo-map",
            "--repo 必须是仓根",
        )
