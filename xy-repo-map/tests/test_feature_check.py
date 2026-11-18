import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from map_fixture import COMMAND, build_map, feature, make_repo


class FeatureCheckTest(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(
            self, {"README.md": "仓库说明。\n"},
            feature("root", "", subrepositories=[], external_dependencies=[]),
        )

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
        self.write("child/readme.md", "child\n")
        child = feature("child", "child", subrepositories=[], external_dependencies=[])
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

        data = build_map(self, self.repo)
        files = [
            path
            for feature_data in data["features"].values()
            for path in feature_data["files"]
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
        self.set_features(feature("root", "", subrepositories=["linked"], external_dependencies=[]))
        subprocess.run(
            ["git", "-C", str(self.repo), "update-index", "--add", "--cacheinfo",
             f"160000,{'1' * 40},linked"],
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
            '"children":[],"items":[]}',
        )

        result = self.check()

        self.assertEqual(result.returncode, 1)
        self.assertIn("无法读取 repo-map/features.json", result.stdout + result.stderr)
        self.assertIn("重复键：id", result.stdout + result.stderr)

    def test_rejects_entries_as_an_unknown_feature_field(self):
        root = feature("root", "", subrepositories=[], external_dependencies=[])
        root["entries"] = []
        self.set_features(root)

        result = self.check()

        self.assertEqual(result.returncode, 1)
        output = result.stdout + result.stderr
        self.assertIn("功能 root", output)
        self.assertIn("directory ''", output)
        self.assertIn("请删除", output)

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
                self.assertIn(f"根功能缺少键 {key}", output)

    def test_requires_items_on_root_and_child_features(self):
        root = feature("root", "", subrepositories=[], external_dependencies=[], items=[])
        del root["items"]
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("功能 root 缺少键 items", result.stdout + result.stderr)

        self.write("pkg/item.py", "value = 1\n")
        child = feature("child", "pkg", items=[])
        del child["items"]
        root = feature(
            "root", "", subrepositories=[], external_dependencies=[],
            children=[child], items=[],
        )
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("功能 child 缺少键 items", result.stdout + result.stderr)

    def assert_invalid_items(self, items, expected):
        self.set_features(feature(
            "root", "", subrepositories=[], external_dependencies=[], items=items,
        ))
        result = self.check()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        for message in expected:
            self.assertIn(message, output)

    def test_rejects_items_that_are_not_arrays_or_items_that_are_not_objects(self):
        cases = [
            ("not-an-array", ("功能 root 的 items 必须是数组",)),
            ([None], ("功能 root 的 items[0] 必须是对象",)),
        ]
        for items, expected in cases:
            with self.subTest(items=items):
                self.assert_invalid_items(items, expected)

    def test_rejects_missing_or_extra_item_keys(self):
        cases = [
            ([{"id": "core", "text": "输出正确结果。"}],
             ("功能 root 的 items[0]", "缺少键 status")),
            ([{"id": "core", "text": "输出正确结果。", "status": "确认", "extra": True}],
             ("功能 root 的 items[0]", "有多余键 extra")),
        ]
        for items, expected in cases:
            with self.subTest(items=items):
                self.assert_invalid_items(items, expected)

    def test_rejects_invalid_or_duplicate_item_ids(self):
        cases = [
            ([{"id": "Bad_ID", "text": "输出正确结果。", "status": "确认"}],
             ("功能 root", "items[0].id")),
            ([
                {"id": "same", "text": "输出正确结果。", "status": "确认"},
                {"id": "same", "text": "输出其他结果。", "status": "确认"},
            ], ("功能 root", "条目 id 重复")),
        ]
        for items, expected in cases:
            with self.subTest(items=items):
                self.assert_invalid_items(items, expected)

    def test_rejects_empty_non_string_and_multiline_item_text(self):
        cases = ("", "  \t", None, "first\nsecond", "first\rsecond",
                 "first\u2028second", "first\u2029second")
        for text in cases:
            with self.subTest(text=text):
                self.assert_invalid_items(
                    [{"id": "core", "status": "确认", "text": text}],
                    ("功能 root 的 items[0]", "items[0].text"),
                )

    def test_rejects_item_status_outside_confirmed_or_draft(self):
        root = feature(
            "root", "", subrepositories=[], external_dependencies=[],
            items=[{"id": "core", "text": "输出正确结果。", "status": "待定"}],
        )
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("功能 root 的 items[0].status", result.stdout + result.stderr)

    def test_accepts_valid_items_with_repeated_ids_on_different_features(self):
        self.write("pkg/item.py", "value = 1\n")
        item = {"id": "core", "text": "输出正确结果。", "status": "确认"}
        root = feature(
            "root", "", subrepositories=[], external_dependencies=[],
            items=[item],
            children=[feature("child", "pkg", items=[item])],
        )
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
            ("原话", "", "契约", "why（原话）必须有非空 quote 和 from"),
            ("原话", "逐字引文", "", "why（原话）必须有非空 quote 和 from"),
            ("确认", "不允许的引文", "本次回答", "why（确认）必须使用空 quote 和非空 from"),
            ("确认", "", "", "why（确认）必须使用空 quote 和非空 from"),
            ("起草", "不允许的引文", "", "why（起草）必须使用空 quote 和空 from"),
            ("未知", "", "", "why.source 只能是 原话、确认、起草"),
        ]
        for source, quote, origin, expected in invalid:
            with self.subTest(source=source, quote=quote, origin=origin):
                root = feature("root", "", subrepositories=[], external_dependencies=[])
                root["why"].update(source=source, quote=quote, **{"from": origin})
                self.set_features(root)
                result = self.check()
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn(f"根功能的 {expected}", output)
                self.assertNotIn("Traceback", output)

    def test_answers_json_references_use_json_answer_keys(self):
        for origin in (
            "/run/decisions/answers.json#answers.L7",
            "/run/decisions/answers.json#answers.P1,answers.P7",
            "/run/decisions/answers.json#answers.L7（说明）",
            "用户原话.md 第 7 行",
        ):
            with self.subTest(origin=origin):
                root = feature("root", "", subrepositories=[], external_dependencies=[], source="确认")
                root["why"]["from"] = origin
                self.set_features(root)
                result = self.check()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        for origin in (
            "/run/decisions/answers.json L7",
            "/run/decisions/answers.json#L7",
        ):
            with self.subTest(origin=origin):
                root = feature("root", "", subrepositories=[], external_dependencies=[], source="确认")
                root["why"]["from"] = origin
                self.set_features(root)
                result = self.check()
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn("root", output)
                self.assertIn("answers.json", output)

    def test_requires_the_root_to_cover_the_repository(self):
        self.set_features(feature("root", "pkg", subrepositories=[], external_dependencies=[]))
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("根", result.stdout + result.stderr)

    def test_rejects_a_null_feature_directory_with_actionable_error(self):
        self.write("pkg/item.py", "value = 1\n")
        root = feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("package", None),
        ])
        self.set_features(root)

        result = self.check()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1)
        self.assertIn("功能 package", output)
        self.assertIn("directory", output)
        self.assertIn("null", output)
        self.assertIn("目录", output)

    def test_rejects_missing_or_non_string_feature_directories(self):
        root = feature("root", "", subrepositories=[], external_dependencies=[])
        del root["directory"]
        self.set_features(root)
        result = self.check()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1)
        self.assertIn("功能 root", output)
        self.assertIn("缺少键 directory", output)
        self.assertIn("填写", output)

        self.write("pkg/item.py", "value = 1\n")
        self.set_features(feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("package", 7),
        ]))
        result = self.check()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1)
        self.assertIn("功能 package", output)
        self.assertIn("directory", output)
        self.assertIn("必须是字符串", output)

    def test_rejects_invalid_feature_directory_values_with_feature_and_path(self):
        cases = [
            ("绝对路径", "/pkg"),
            ("上级路径", "../pkg"),
            ("反斜线", "pkg\\part"),
            ("空段", "pkg//part"),
            ("点段", "pkg/./part"),
            ("结尾斜线", "pkg/"),
        ]
        self.write("pkg/item.py", "value = 1\n")
        for name, directory in cases:
            with self.subTest(name=name):
                root = feature("root", "", subrepositories=[], external_dependencies=[], children=[
                    feature("package", directory),
                ])
                self.set_features(root)
                result = self.check()
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn("功能 package", output)
                self.assertIn(repr(directory), output)
                self.assertIn("安全的仓内相对目录", output)

    def test_rejects_duplicate_feature_directories_with_both_features_named(self):
        self.write("pkg/first.py", "value = 1\n")
        self.set_features(feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("first", "pkg"),
            feature("second", "pkg"),
        ]))

        result = self.check()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1)
        self.assertIn("first", output)
        self.assertIn("second", output)
        self.assertIn("pkg", output)
        self.assertIn("目录重复", output)

    def test_rejects_a_feature_directory_without_tracked_files_and_says_how_to_fix_it(self):
        self.write("elsewhere/item.py", "value = 1\n")
        self.set_features(feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("package", "pkg"),
        ]))

        result = self.check()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1)
        self.assertIn("功能 package", output)
        self.assertIn("pkg", output)
        self.assertIn("请把 package 的文件放进目录 'pkg' 并提交", output)
        self.assertIn("或把 package 的 directory 改为其文件实际所在的目录", output)

    def test_child_directory_must_be_nested_under_its_parent_directory(self):
        self.write("pkg/parent.py", "parent = 1\n")
        self.write("other/item.py", "value = 1\n")
        root = feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("package", "pkg", children=[feature("outside", "other")]),
        ])
        self.set_features(root)

        result = self.check()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1)
        self.assertIn("功能 outside", output)
        self.assertIn("other", output)
        self.assertIn("pkg", output)
        self.assertIn("不在上级", output)
        self.assertIn("请", output)

    def test_child_directory_cannot_skip_an_intermediate_feature_directory(self):
        self.write("a/b/item.py", "value = 1\n")
        self.write("a/b/c/item.py", "value = 2\n")
        root = feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("parent", "a", children=[feature("child", "a/b/c")]),
            feature("middle", "a/b"),
        ])
        self.set_features(root)

        result = self.check()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1)
        self.assertIn("功能 child", output)
        self.assertIn("a/b/c", output)
        self.assertIn("middle", output)
        self.assertIn("最近", output)
        self.assertIn("请", output)

    def test_child_directory_may_be_below_ordinary_directories_inside_parent(self):
        self.write("pkg/src/module/main.py", "value = 1\n")
        self.set_features(feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("package", "pkg", children=[feature("module", "pkg/src/module")]),
        ]))

        result = self.check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_a_nested_git_repository(self):
        nested = self.repo / "nested"
        nested.mkdir()
        subprocess.run(["git", "-C", str(nested), "init", "-q"], check=True)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("子仓", result.stdout + result.stderr)

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
