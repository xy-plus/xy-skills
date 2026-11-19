from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPOSITORY_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists())
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "tests"))

from map_fixture import COMMAND, ROOT
from repo_map_lib.constants import LINE_NAMES
from repo_map_lib.map_data import _file_category, _read_source  # noqa: E402
from repo_map_lib.count_lines.metrics import count_file  # noqa: E402


class RealRepositoryTest(unittest.TestCase):
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

        self.assertIn("self-delete", map_data["features"])
        self.assertEqual(
            set(map_data["features"]["self-delete"]["files"]),
            {
                "xy-handoff/self_delete/delete_self.py",
                "xy-handoff/self_delete/tests/test_delete_self.py",
            },
        )

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
            for path in feature["files"]
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
