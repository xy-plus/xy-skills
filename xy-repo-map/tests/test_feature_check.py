"""Check feature-list rules through the public repository command."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from map_fixture import feature


ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "xy-repo-map" / "repo_map.py"


class FeatureCheckTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "sample-repo"
        self.repo.mkdir()
        self.write("README.md", "仓库说明。\n")
        self.write("repo-map/features.json", json.dumps(feature("root", ""), ensure_ascii=False))
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

    def test_accepts_a_valid_root_feature(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("检查通过", result.stdout)

    def test_rejects_an_extra_feature_key(self):
        root = feature("root", "")
        root["legacy"] = "旧字段"
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("legacy", result.stdout + result.stderr)

    def test_rejects_a_missing_feature_key(self):
        root = feature("root", "")
        del root["what"]
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("what", result.stdout + result.stderr)

    def test_rejects_an_invalid_identifier_and_duplicate_identifier(self):
        root = feature("Bad_ID", "")
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("id", result.stdout + result.stderr)
        root = feature("root", "", children=[feature("same", "pkg"), feature("same", "other")])
        self.write("pkg/item.py", "value = 1\n")
        self.write("other/item.py", "value = 2\n")
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("same", result.stdout + result.stderr)

    def test_accepts_only_valid_why_source_combinations(self):
        for source in ("原话", "确认", "起草"):
            with self.subTest(source=source):
                self.set_features(feature("root", "", source=source))
                result = self.check()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
                root = feature("root", "")
                root["why"].update(source=source, quote=quote, **{"from": origin})
                self.set_features(root)
                result = self.check()
                self.assertEqual(result.returncode, 1)
                self.assertIn("why", result.stdout + result.stderr)

    def test_requires_the_root_to_cover_the_repository(self):
        self.set_features(feature("root", "pkg"))
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("根", result.stdout + result.stderr)

    def test_rejects_missing_duplicate_and_outside_directories(self):
        cases = [
            (feature("root", "", children=[feature("missing", "absent")]), "absent"),
            (feature("root", "", children=[feature("first", "pkg"), feature("second", "pkg")]), "pkg"),
            (feature("root", "", children=[feature("outer", "pkg", [feature("outside", "other")])]), "other"),
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
            (feature("root", "", children=[feature("pkg", "pkg", entries=["pkg/missing.py"])]), "missing.py"),
            (feature("root", "", children=[feature("pkg", "pkg", entries=["other/owned.py"])]), "other/owned.py"),
        ]
        for root, expected in cases:
            with self.subTest(entry=expected):
                self.set_features(root)
                result = self.check()
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stdout + result.stderr)

    def test_rejects_an_entry_inside_another_directory_feature(self):
        self.write("pkg/owned.py", "value = 1\n")
        self.write("other/owned.py", "value = 2\n")
        root = feature("root", "", children=[
            feature("pkg", "pkg", [feature("inner", None)], ["other/owned.py"]),
            feature("other", "other"),
        ])
        self.set_features(root)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("other/owned.py", result.stdout + result.stderr)

    def test_rejects_entries_on_a_leaf_directory_feature(self):
        self.write("pkg/owned.py", "value = 1\n")
        self.set_features(feature("root", "", children=[feature("pkg", "pkg", entries=["pkg/owned.py"])]))
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
                    ".cs", ".rb", ".php", ".scala", ".lua")
        for suffix in suffixes:
            self.write(f"src/legacy{suffix}", "unsupported source\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "src"], check=True)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                self.assertIn(f"legacy{suffix}", result.stdout + result.stderr)

    def test_rejects_and_lists_a_typescript_source_file(self):
        self.write("src/application.ts", "const answer = 42;\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "src/application.ts"], check=True)

        result = self.check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("src/application.ts", result.stdout + result.stderr)

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


if __name__ == "__main__":
    unittest.main()
