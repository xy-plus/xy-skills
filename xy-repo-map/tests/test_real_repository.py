"""Check real-repository coverage, line totals, and the deferred cleanup reminder."""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "xy-repo-map" / "repo_map.py"
SKILL_DIRECTORIES = (
    "sync-think", "xy-cleanup", "xy-goal", "xy-handoff", "xy-iron-law",
    "xy-repo-map", "xy-repo-rules", "xy-review", "xy-runs", "xy-workflow",
)
LINE_NAMES = ("code", "docs", "comments", "tests")


class RealRepositoryTest(unittest.TestCase):
    def test_xy_skills_meets_feature_map_acceptance(self):
        checked = subprocess.run(
            [sys.executable, "-B", str(COMMAND), "check", "--repo", str(ROOT)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "rendered"
            rendered = subprocess.run(
                [sys.executable, "-B", str(COMMAND), "render", "--repo", str(ROOT), "--out", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
            map_data = json.loads((output / "map-data.json").read_text(encoding="utf-8"))

        feature_path = ROOT / "repo-map" / "features.json"
        feature_tree = json.loads(feature_path.read_text(encoding="utf-8"))
        by_title = {}

        def index_features(feature):
            by_title[feature["title"]] = feature
            for child in feature["children"]:
                index_features(child)

        index_features(feature_tree)

        goal = by_title["长目标契约"]
        self.assertEqual(goal["entries"], ["xy-goal/README.md"])
        for title in ("逐字记录我的原话", "事项对账与验收", "定时自查"):
            with self.subTest(goal_feature=title):
                self.assertEqual(by_title[title]["entries"], ["xy-goal/SKILL.md", "xy-goal/goal.py"])
        self.assertTrue(all(
            not any(value for value in map_data["features"][by_title[title]["id"]]["lines"]["total"].values())
            for title in ("逐字记录我的原话", "事项对账与验收", "定时自查")
        ))

        runs = by_title["运行目录管理"]
        self.assertEqual(runs["entries"], [])
        self.assertEqual(by_title["文件去处规则"]["entries"], ["xy-runs/SKILL.md"])
        self.assertEqual(by_title["定期清理运行目录"]["entries"], [
            "xy-runs/clean.py", "xy-runs/README.md", "xy-runs/systemd/",
        ])

        self.assertEqual(by_title["校验功能清单"]["entries"], [
            "xy-repo-map/repo_map_lib/features.py",
            "xy-repo-map/repo_map_lib/map_data.py",
        ])
        self.assertIn(
            "xy-repo-map/repo_map_lib/templates/feature-tree.html",
            by_title["出图"]["entries"],
        )

        def collect_entries(feature):
            yield from feature["entries"]
            for child in feature["children"]:
                yield from collect_entries(child)

        for entry in collect_entries(feature_tree):
            self.assertNotIn("tests/", entry)
            self.assertNotIn("test_", Path(entry.rstrip("/")).name)

        self.assertEqual(by_title["会话任务交接"]["why"]["from"],
                         "20261025-070412-xy-skills-task-handoff 原话 1")
        self.assertEqual(by_title["收拾仓库"]["why"]["source"], "原话")
        self.assertEqual(by_title["收拾仓库"]["why"]["quote"],
                         "ai写的代码太多了，应该怎么整理，让仓库干净？")
        self.assertEqual(by_title["收拾仓库"]["why"]["from"],
                         "20261031-002858-xy-skills-cleanup-skill 原话 1")
        self.assertEqual(by_title["仓库功能图"]["why"]["from"],
                         "20261031-002858-xy-skills-cleanup-skill 原话 5")
        self.assertEqual(by_title["出图"]["why"]["from"],
                         "20261031-002858-xy-skills-cleanup-skill 原话 159")
        self.assertEqual(by_title["仓库清扫"]["why"]["from"],
                         "20261110-070411-xy-skills-repo-map-cleanup-redesign 原话 1")

        directory_features = []

        def collect(feature):
            if feature["directory"] is not None:
                directory_features.append(feature["directory"])
            for child in feature["children"]:
                collect(child)

        collect(feature_tree)
        for directory in SKILL_DIRECTORIES:
            with self.subTest(skill_directory=directory):
                self.assertEqual(directory_features.count(directory), 1)
        self.assertEqual(len(directory_features) - 1, len(SKILL_DIRECTORIES))

        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True, text=True, check=True,
        ).stdout.split("\0")
        tracked = {path for path in tracked if path}
        assigned = Counter(
            path
            for feature in map_data["features"].values()
            for group in feature["files"].values()
            for path in group
        )
        self.assertEqual(set(assigned), tracked)
        self.assertEqual({path for path, count in assigned.items() if count != 1}, set())

        script = (
            "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "from repo_map_lib.map_data import _file_category, _source_text; "
            "from repo_map_lib.metrics import count_file; "
            "paths=[p for p in Path(sys.argv[3]).read_text().split('\\0') if p]; "
            "totals=dict.fromkeys(('code','docs','comments','tests'),0); "
            "[totals.__setitem__(k,totals[k]+getattr(count_file(p,_file_category(p),_source_text(sys.argv[2],p) or ''),k)) for p in paths for k in totals]; "
            "print(json.dumps(totals))"
        )
        with tempfile.TemporaryDirectory() as temporary:
            paths_file = Path(temporary) / "paths.txt"
            paths_file.write_text("\0".join(sorted(tracked)), encoding="utf-8")
            counted = subprocess.run(
                [sys.executable, "-B", "-c", script, str(ROOT / "xy-repo-map"), str(ROOT), str(paths_file)],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(counted.returncode, 0, counted.stdout + counted.stderr)
        expected_lines = json.loads(counted.stdout)
        root_lines = map_data["features"][map_data["root"]]["lines"]["total"]
        self.assertEqual({name: root_lines[name] for name in LINE_NAMES}, expected_lines)

        by_title = {feature["title"]: feature_id for feature_id, feature in map_data["features"].items()}
        cleanup_id = by_title["仓库清扫"]
        reminder_id = next(child for child in map_data["features"][cleanup_id]["children"]
                           if map_data["features"][child]["title"] == "只审变过的功能")
        self.assertIn("等你拍板", map_data["features"][reminder_id]["markers"])
        self.assertIn("还没实现", map_data["features"][reminder_id]["markers"])
        self.assertEqual(map_data["markers"]["还没实现"], 1)
        self.assertEqual(map_data["markers"]["没归到功能的代码"], 0)

        cleanup_id = by_title["仓库清扫"]
        cleanup_uses = {
            map_data["features"][use["feature"]]["title"]: use["by"]
            for use in map_data["features"][cleanup_id]["uses"]
        }
        self.assertEqual(cleanup_uses["仓库功能图"], "import")
        self.assertEqual(cleanup_uses["对抗审查"], "import")


if __name__ == "__main__":
    unittest.main()
