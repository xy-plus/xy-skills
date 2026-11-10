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
            "from repo_map_lib.map_data import _file_category; "
            "from repo_map_lib.metrics import count_file; "
            "paths=[p for p in Path(sys.argv[3]).read_text().split('\\0') if p]; "
            "totals=dict.fromkeys(('code','docs','comments','tests'),0); "
            "[totals.__setitem__(k,totals[k]+getattr(count_file(p,_file_category(p),(Path(sys.argv[2])/p).read_bytes()),k)) for p in paths for k in totals]; "
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


if __name__ == "__main__":
    unittest.main()
