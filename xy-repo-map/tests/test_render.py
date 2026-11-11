import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from map_fixture import COMMAND, feature, make_repo, make_workspace


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.repository = make_repo(self, {
            "README.md": "Demo repository.\n",
            "alpha/main.py": "value = 1\n",
            "branch/approved.py": "value = 2\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("alpha", "alpha"),
            feature("branch", "branch", children=[
                feature("approved", None, entries=["branch/approved.py"]),
                feature("future", None, source="起草"),
            ]),
        ]))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "rendered"

    def render(self, output=None):
        return subprocess.run(
            ["python3", "-B", str(COMMAND), "render", "--repo", str(self.repository),
             "--out", str(output or self.output)],
            capture_output=True, text=True, check=False,
        )

    def test_render_writes_map_data_and_a_self_contained_page(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads((self.output / "map-data.json").read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"root", "features", "markers"})
        self.assertEqual(data["root"], "root")
        self.assertEqual(data["markers"]["等你拍板"], 1)
        page = (self.output / "index.html").read_text(encoding="utf-8")
        embedded = re.search(r'<script id="map-data" type="application/json">(.*?)</script>',
                             page, re.DOTALL)
        self.assertIsNotNone(embedded)
        page_data = json.loads(embedded.group(1))
        expected = json.loads(json.dumps(data))
        for item in expected["features"].values():
            del item["files"]
        self.assertEqual(page_data, expected)
        self.assertNotIn("<script src=", page)
        self.assertNotIn("<link rel=\"stylesheet\" href=", page)

    def test_render_does_not_write_a_page_when_the_feature_list_is_invalid(self):
        feature_path = self.repository / "repo-map" / "features.json"
        feature_path.write_text("{}", encoding="utf-8")
        result = self.render()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.output.exists())

    def test_render_rejects_a_nonempty_output_directory(self):
        workspace = make_workspace(self)
        for command, repository in (
            ("render", self.repository),
            ("render-workspace", workspace),
        ):
            with self.subTest(command=command):
                output = self.output / command
                output.mkdir(parents=True)
                keep_file = output / "keep.txt"
                keep_file.write_text("keep", encoding="utf-8")
                result = subprocess.run(
                    ["python3", "-B", str(COMMAND), command,
                     "--repo", str(repository), "--out", str(output)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("--out 必须不存在或为空", result.stdout + result.stderr)
                self.assertEqual(keep_file.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
