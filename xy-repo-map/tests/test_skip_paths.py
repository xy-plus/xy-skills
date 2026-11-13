"""Test repository-declared paths excluded from map and cleanup inputs."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from map_fixture import COMMAND, build_map, feature, make_repo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "xy-cleanup"))
from repo_map_lib.map_data import build_map as derive_map  # noqa: E402
from repo_map_lib.map_data import file_category  # noqa: E402
from approvals import feature_fingerprints, feature_values  # noqa: E402


def write_skip_list(repository, paths):
    skip_file = repository / "repo-map" / "skip.json"
    skip_file.write_text(json.dumps(paths, ensure_ascii=False), encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "repo-map/skip.json"], check=True,
    )


class SkipPathsTest(unittest.TestCase):
    def test_map_commands_require_skip_file_and_explain_empty_list(self):
        repository = make_repo(
            self, {"README.md": "Guide.\n"},
            feature("root", "", subrepositories=[], external_dependencies=[]),
        )
        (repository / "repo-map/skip.json").unlink()

        for arguments in (
            ["check", "--repo", str(repository)],
            ["render", "--repo", str(repository), "--out", str(repository / "rendered")],
            ["render-workspace", "--repo", str(repository), "--out", str(repository / "workspace")],
        ):
            with self.subTest(command=arguments[0]):
                result = subprocess.run(
                    [sys.executable, "-B", str(COMMAND), *arguments],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("没有要跳过的路径就写 []", result.stdout + result.stderr)

    def test_skip_file_and_directory_are_other_zero_count_and_not_read(self):
        repository = make_repo(self, {
            "alpha/main.py": "# visible-tool\nimport pkg.hidden\n",
            "reader/guide.md": "See pkg/hidden.py and archive/log.md.\n",
            "pkg/hidden.py": (
                "import beta.impl\n"
                "See beta/impl.py and secret-tool.\n"
            ),
            "beta/impl.py": "value = 1\n",
            "archive/log.md": "secret-tool\n" * 20,
        }, feature("root", "", subrepositories=[], external_dependencies=["visible-tool"], children=[
            feature("alpha", "alpha", children=[
                feature("source", None, entries=["alpha/main.py"]),
            ]),
            feature("reader", "reader", children=[
                feature("reader-doc", None, entries=["reader/guide.md"]),
            ]),
            feature("hidden", "pkg", children=[
                feature("hidden-file", None, entries=["pkg/hidden.py"]),
            ]),
            feature("beta", "beta"),
            feature("archive", "archive"),
        ]))
        write_skip_list(repository, ["pkg/hidden.py", "archive/"])

        hidden_file = repository / "pkg/hidden.py"
        archive_file = repository / "archive/log.md"
        original_read_bytes = Path.read_bytes

        def reject_skipped_content(path):
            if path in (hidden_file, archive_file):
                raise AssertionError(f"read skipped file: {path}")
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", reject_skipped_content):
            data = derive_map(repository)

        self.assertEqual(file_category(repository, "pkg/hidden.py"), "other")
        self.assertEqual(file_category(repository, "archive/log.md"), "other")
        self.assertEqual(data["features"]["hidden-file"]["lines"]["own"], {
            "code": 0, "docs": 0, "comments": 0, "tests": 0,
        })
        self.assertEqual(data["features"]["archive"]["lines"]["own"], {
            "code": 0, "docs": 0, "comments": 0, "tests": 0,
        })
        self.assertEqual(data["features"]["hidden-file"]["uses"], [])
        self.assertEqual(data["features"]["hidden-file"]["external_uses"], [])
        self.assertEqual(data["features"]["source"]["uses"], [
            {"feature": "hidden", "by": "mention"},
            {"feature": "hidden-file", "by": "import"},
        ])
        self.assertEqual(data["features"]["reader-doc"]["uses"], [
            {"feature": "hidden", "by": "mention"},
            {"feature": "hidden-file", "by": "mention"},
            {"feature": "archive", "by": "mention"},
        ])

    def test_skip_list_requires_safe_unique_existing_file_or_directory_paths(self):
        repository = make_repo(self, {
            "alpha/main.py": "value = 1\n",
            "docs/guide.md": "Guide.\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[]))
        invalid_lists = (
            ("not an array", "必须是数组"),
            ([7], "必须是字符串"),
            (["missing.txt"], "不存在或没有被跟踪文件"),
            (["alpha/main.py", "alpha/main.py"], "不能重复"),
            (["/alpha/main.py"], "不安全"),
            (["alpha\\main.py"], "不安全"),
            (["alpha//main.py"], "不安全"),
            (["alpha/./main.py"], "不安全"),
            (["alpha/../main.py"], "不安全"),
        )

        for paths, expected in invalid_lists:
            with self.subTest(paths=paths):
                write_skip_list(repository, paths)
                with self.assertRaisesRegex(ValueError, expected):
                    derive_map(repository)

    def test_empty_skip_list_leaves_map_unchanged(self):
        repository = make_repo(self, {
            "docs/guide.md": "# Guide\nA page.\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[]))
        write_skip_list(repository, [])

        data = build_map(self, repository)

        self.assertEqual(data["features"]["root"]["lines"]["own"]["docs"], 2)

    def test_skipped_content_and_skip_file_do_not_change_approval_fingerprint(self):
        repository = make_repo(self, {
            "README.md": "Root guide.\n",
            "docs/experiments/run.md": "First result.\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("experiments", "docs/experiments"),
        ]))
        write_skip_list(repository, ["docs/experiments/"])
        features, _ = feature_values(repository)

        first = feature_fingerprints(
            repository, derive_map(repository), features,
        )
        (repository / "docs/experiments/run.md").write_text(
            "A different experiment log.\nMore details.\n", encoding="utf-8",
        )
        skip_file = repository / "repo-map/skip.json"
        skip_file.write_text('[\n  "docs/experiments/"\n]\n', encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repository), "add", "docs/experiments/run.md", "repo-map/skip.json"],
            check=True,
        )

        second = feature_fingerprints(
            repository, derive_map(repository), features,
        )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
