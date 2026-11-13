"""Check real-repository validation, file assignment, and line totals."""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "xy-repo-map" / "repo_map.py"
LINE_NAMES = ("code", "docs", "comments", "tests")

sys.path.insert(0, str(ROOT / "xy-repo-map"))
from repo_map_lib.map_data import _file_category, _read_source  # noqa: E402
from repo_map_lib.metrics import count_file  # noqa: E402


class RealRepositoryTest(unittest.TestCase):
    def test_cleanup_text_capabilities_are_recorded_in_the_feature_catalog(self):
        catalog = json.loads((ROOT / "repo-map" / "features.json").read_text(encoding="utf-8"))

        def flatten(feature):
            yield feature
            for child in feature["children"]:
                yield from flatten(child)

        features = {feature["id"]: feature for feature in flatten(catalog)}
        self.assertEqual(
            features["count-lines"]["what"],
            "每个功能的代码、测试、文档、注释各多少行，文档多少字。",
        )
        self.assertEqual(
            features["xy-cleanup"]["what"],
            "按功能范围审查并清理代码、测试、文档和 TODO：每个功能估同样的「要什么」最小要多大、把更小的做法写成提议，每份文档写一份意思不变、字最少的改写稿，逐个看共用文件要不要按功能拆开，汇总要我定的改动问清；保留审查规则改过后的已知漏项，作对照输入。",
        )
        self.assertEqual(
            features["cleanup-report-check"]["what"],
            "清扫审查报告是否覆盖了范围内每个功能、每个直属文件和 TODO 的每个非空行，零删减的功能有没有逐条推翻的剃刀复审，估得更小的做法有没有写成功能级提议，留下的代码和仓里别处逐字重复时有没有写出另一份，剃刀理由有没有逐字相同的套话，文档改写稿齐不齐、比原文短的有没有写成提议，格式和决定合不合规，用脚本查。",
        )
        self.assertEqual(
            features["decision-page"]["what"],
            "清扫里要我定的事放在一个网页上，每条有自己的选项和一个可以自己写的框，我点完提交就行；没提交的选择和填写存在浏览器里，在浏览器能存时，刷新或重开页面能恢复。拒绝含重复键的 JSON。改写文档的提议逐段对照显示原文和改写。",
        )
        self.assertEqual(
            features["skip-paths"],
            {
                "id": "skip-paths",
                "title": "整块跳过的路径",
                "what": "仓库在 `repo-map/skip.json` 里列出的路径不计行数和字数，清扫也不审。",
                "why": {
                    "text": "有些内容（比如实验记录）不需要参与优化，要能整块跳过。",
                    "source": "原话",
                    "quote": "需要有白名单，比如quant workspace下面的实验文档就是不需要参与优化的，就应该被全量跳过",
                    "from": "20261113-091419-xy-skills-cleanup-text 原话 3",
                },
                "directory": None,
                "entries": [
                    "xy-repo-map/repo_map_lib/skip_paths.py",
                    "xy-repo-map/tests/test_skip_paths.py",
                ],
                "children": [],
            },
        )

    def test_xy_skills_validates_assigns_files_and_counts_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "rendered"
            rendered = subprocess.run(
                [sys.executable, "-B", str(COMMAND), "render", "--repo", str(ROOT), "--out", str(output)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
            map_data = json.loads((output / "map-data.json").read_text(encoding="utf-8"))

        listed_paths = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\0")
        tracked_paths = {path for path in listed_paths if path}
        assigned_paths = Counter(
            path
            for feature in map_data["features"].values()
            for group in feature["files"].values()
            for path in group
        )
        self.assertEqual(set(assigned_paths), tracked_paths)
        self.assertTrue(all(count == 1 for count in assigned_paths.values()))

        expected_lines = dict.fromkeys(LINE_NAMES, 0)
        for path in sorted(tracked_paths):
            category, source = _read_source(ROOT, path, _file_category(path))
            counts = count_file(path, category, source)
            for name in LINE_NAMES:
                expected_lines[name] += getattr(counts, name)
        root_lines = map_data["features"][map_data["root"]]["lines"]["total"]
        self.assertEqual({name: root_lines[name] for name in LINE_NAMES}, expected_lines)
