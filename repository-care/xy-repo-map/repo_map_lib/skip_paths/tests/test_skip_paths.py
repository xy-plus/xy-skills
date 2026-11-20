import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

REPOSITORY_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "tests"))
from map_fixture import (  # noqa: E402
    COMMAND,
    empty_root_feature,
    feature,
    make_repo,
    root_feature as make_root_feature,
)

from repo_map_lib.map_data import build_map as derive_map  # noqa: E402


def write_skip_list(repository, paths):
    skip_file = repository / "repo-map" / "skip.json"
    skip_file.write_text(json.dumps(paths), encoding="utf-8")


class SkipPathsTest(unittest.TestCase):
    def test_map_commands_require_skip_file_and_explain_empty_list(self):
        repository = make_repo(
            self,
            {"README.md": "Guide.\n"},
            empty_root_feature(),
        )
        (repository / "repo-map/skip.json").unlink()

        for arguments in (
            ["check", "--repo", str(repository)],
            [
                "render",
                "--repo",
                str(repository),
                "--out",
                str(repository / "rendered"),
            ],
            [
                "render-workspace",
                "--repo",
                str(repository),
                "--out",
                str(repository / "workspace"),
            ],
        ):
            with self.subTest(command=arguments[0]):
                result = subprocess.run(
                    [sys.executable, "-B", str(COMMAND), *arguments],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("没有要跳过的路径就写 []", result.stdout + result.stderr)

    def test_skip_file_and_directory_are_other_zero_count_and_not_read(self):
        repository = make_repo(
            self,
            {
                "alpha/source/main.py": "# visible-tool\nimport pkg.hidden_file.hidden\n",
                "reader/guide/guide.md": (
                    "See `pkg/hidden_file/hidden.py` and `archive/log.md`.\n"
                ),
                "pkg/hidden_file/hidden.py": (
                    "import beta.impl\nSee beta/impl.py and secret-tool.\n"
                ),
                "beta/impl.py": "value = 1\n",
                "archive/log.md": "secret-tool\n" * 20,
            },
            make_root_feature(
                "root",
                "",
                subrepositories=[],
                external_dependencies=["visible-tool"],
                children=[
                    feature(
                        "alpha",
                        "alpha",
                        children=[
                            feature(
                                "source",
                                "alpha/source",
                                children=[],
                                source="原话",
                                items=[],
                            ),
                        ],
                        source="原话",
                        items=[],
                    ),
                    feature(
                        "reader",
                        "reader",
                        children=[
                            feature(
                                "reader-doc",
                                "reader/guide",
                                children=[],
                                source="原话",
                                items=[],
                            ),
                        ],
                        source="原话",
                        items=[],
                    ),
                    feature(
                        "hidden",
                        "pkg",
                        children=[
                            feature(
                                "hidden-file",
                                "pkg/hidden_file",
                                children=[],
                                source="原话",
                                items=[],
                            ),
                        ],
                        source="原话",
                        items=[],
                    ),
                    feature("beta", "beta", children=[], source="原话", items=[]),
                    feature("archive", "archive", children=[], source="原话", items=[]),
                ],
                source="原话",
                items=[],
            ),
        )
        write_skip_list(repository, ["pkg/hidden_file/hidden.py", "archive/"])

        hidden_file = repository / "pkg/hidden_file/hidden.py"
        archive_file = repository / "archive/log.md"
        original_read_bytes = Path.read_bytes

        def reject_skipped_content(path):
            if path in (hidden_file, archive_file):
                raise AssertionError(f"read skipped file: {path}")
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", reject_skipped_content):
            data = derive_map(repository)

        self.assertEqual(
            data["features"]["hidden-file"]["lines"]["own"],
            {
                "code": 0,
                "docs": 0,
                "comments": 0,
                "tests": 0,
            },
        )
        self.assertEqual(
            data["features"]["archive"]["lines"]["own"],
            {
                "code": 0,
                "docs": 0,
                "comments": 0,
                "tests": 0,
            },
        )
        self.assertEqual(data["features"]["hidden-file"]["uses"], [])
        self.assertEqual(data["features"]["hidden-file"]["external_uses"], [])
        self.assertEqual(
            data["features"]["source"]["uses"],
            [
                {"feature": "hidden-file", "by": "import"},
            ],
        )
        self.assertEqual(
            data["features"]["reader-doc"]["uses"],
            [
                {"feature": "hidden-file", "by": "mention"},
                {"feature": "archive", "by": "mention"},
            ],
        )

    def test_skip_list_requires_safe_unique_existing_file_or_directory_paths(self):
        repository = make_repo(
            self,
            {
                "alpha/main.py": "value = 1\n",
                "docs/guide.md": "Guide.\n",
            },
            empty_root_feature(),
        )
        invalid_lists = (
            ([7], "必须是字符串"),
            (["missing.txt"], "不存在或没有被跟踪文件"),
            (["alpha/main.py", "alpha/main.py"], "不能重复"),
            (["/alpha/main.py"], "不安全"),
            (["alpha//main.py"], "不安全"),
            (["alpha/./main.py"], "不安全"),
            (["alpha/../main.py"], "不安全"),
        )

        for paths, expected in invalid_lists:
            with self.subTest(paths=paths):
                write_skip_list(repository, paths)
                with self.assertRaisesRegex(ValueError, expected):
                    derive_map(repository)

    def test_skipped_content_and_skip_file_format_do_not_change_derived_map(self):
        repository = make_repo(
            self,
            {
                "README.md": "Root guide.\n",
                "docs/experiments/run.md": "First result.\n",
            },
            make_root_feature(
                "root",
                "",
                subrepositories=[],
                external_dependencies=[],
                children=[
                    feature(
                        "experiments",
                        "docs/experiments",
                        children=[],
                        source="原话",
                        items=[],
                    ),
                ],
                source="原话",
                items=[],
            ),
        )
        write_skip_list(repository, ["docs/experiments/"])
        first = derive_map(repository)
        (repository / "docs/experiments/run.md").write_text(
            "A different experiment log.\nMore details.\n",
            encoding="utf-8",
        )
        skip_file = repository / "repo-map/skip.json"
        skip_file.write_text('[\n  "docs/experiments/"\n]\n', encoding="utf-8")
        second = derive_map(repository)

        self.assertEqual(first, second)
