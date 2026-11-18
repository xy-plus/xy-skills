import json
import re
import subprocess
import unittest

from map_fixture import COMMAND, build_map, feature, make_repo


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.repository = make_repo(self, {
            "README.md": "Demo repository.\n",
            "alpha/main.py": "value = 1\n",
            "branch/approved/main.py": "value = 2\n",
            "branch/future/note.bin": "not source\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("alpha", "alpha"),
            feature("branch", "branch", children=[
                feature("approved", "branch/approved"),
                feature("future", "branch/future", source="起草", items=[{
                    "id": "draft-item",
                    "text": "用户可以检查待确认的内容。",
                    "status": "起草",
                }]),
            ]),
        ]))
        self.output = self.repository.parent / "rendered"

    def run_command(self, command, repository, output):
        return subprocess.run(
            ["python3", "-B", str(COMMAND), command, "--repo", str(repository),
             "--out", str(output)],
            capture_output=True, text=True, check=False,
        )

    def test_render_writes_map_data_and_a_self_contained_page(self):
        result = self.run_command("render", self.repository, self.output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads((self.output / "map-data.json").read_text(encoding="utf-8"))
        self.assertEqual(data, build_map(self, self.repository))
        self.assertEqual(data["features"]["root"].get("items"), [])
        self.assertEqual(data["features"]["future"].get("items"), [{
            "id": "draft-item",
            "text": "用户可以检查待确认的内容。",
            "status": "起草",
        }])
        self.assertIsInstance(data["features"]["future"]["files"], list)
        self.assertEqual(data["markers"], {"等你拍板": 1})
        page = (self.output / "index.html").read_text(encoding="utf-8")
        embedded = re.search(r'<script id="map-data" type="application/json">(.*?)</script>',
                             page, re.DOTALL)
        self.assertIsNotNone(embedded)
        page_data = json.loads(embedded.group(1))
        for item in data["features"].values():
            del item["files"]
        self.assertEqual(page_data, data)
        self.assertNotIn("<script src=", page)
        self.assertNotIn("<link rel=\"stylesheet\" href=", page)
        self.assertIn("本级自己的部分", page)
        self.assertNotIn("没细分到子功能的部分", page)
        self.assertNotIn("还没实现", page)
        self.assertNotIn("没归到功能的代码或测试", page)

    def test_render_does_not_write_a_page_when_the_feature_list_is_invalid(self):
        feature_path = self.repository / "repo-map" / "features.json"
        feature_path.write_text("{}", encoding="utf-8")
        result = self.run_command("render", self.repository, self.output)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.output.exists())

    def test_render_rejects_a_nonempty_output_directory(self):
        self.output.mkdir()
        keep_file = self.output / "keep.txt"
        keep_file.write_text("keep", encoding="utf-8")
        result = self.run_command("render", self.repository, self.output)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--out 必须不存在或为空", result.stdout + result.stderr)
        self.assertEqual(keep_file.read_text(encoding="utf-8"), "keep")
