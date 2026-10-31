"""Real LikeC4 CLI integration; failure to run npx is a test failure."""
from pathlib import Path
import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
from time import perf_counter
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map import main
from repo_map_lib.checker import check_repository
from repo_map_lib.constants import STANDARD_KINDS
from repo_map_lib.render_command import render_repository


def run_cli(*arguments):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(["repo_map.py", *arguments])


def read_page_data(site):
    html = (site / "index.html").read_text(encoding="utf-8")
    match = re.search(r'<script id="page-data" type="application/json">(.*?)</script>',
                      html, re.DOTALL)
    if match is None:
        raise AssertionError("index.html 缺少内嵌页面数据")
    return json.loads(match.group(1))


def assert_page_matches_map(test, site, expected_mode):
    map_data = json.loads((site / "map-data.json").read_text(encoding="utf-8"))
    page_data = read_page_data(site)
    test.assertEqual(map_data["mode"], expected_mode)
    test.assertEqual(page_data["mode"], expected_mode)
    test.assertEqual(set(page_data["nodes"]), set(map_data["nodes"]))
    for element_id, map_node in map_data["nodes"].items():
        for field in ("title", "kind", "parent", "own", "total"):
            test.assertEqual(page_data["nodes"][element_id][field], map_node[field])
    return map_data, page_data


class LikeC4IntegrationTest(unittest.TestCase):
    def test_real_cli_validates_exports_and_renders_an_offline_draft(self):
        with tempfile.TemporaryDirectory(prefix="xy-repo-map-likec4-") as temporary:
            root = Path(temporary) / "demo"
            root.mkdir()
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "tool.py").write_text(
                '"""Tool module."""\ndef run():\n    """Run one tool."""\n    return 1\n',
                encoding="utf-8")
            (root / "tests" / "test_tool.py").write_text(
                "def test_run():\n    assert True\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "src", "tests"],
                           check=True, capture_output=True)

            repo_map = root / "repo-map"
            repo_map.mkdir()
            (repo_map / "likec4.config.json").write_text('{"name":"demo"}\n', encoding="utf-8")
            (repo_map / "kinds.c4").write_text(STANDARD_KINDS, encoding="utf-8")
            (repo_map / "approvals.txt").write_text("", encoding="utf-8")
            (repo_map / "model.c4").write_text(
                "model {\n  demo = node 'Demo' {\n    description 'A small example repository.'\n"
                "    tool = node 'Tool' {\n      description 'Runs a small tool.'\n"
                "      link ../src/tool.py 'code'\n      link ../tests/test_tool.py 'tests'\n"
                "    }\n  }\n}\n", encoding="utf-8")

            problems, model, _, _ = check_repository(root, False)
            self.assertEqual(problems, [], problems)
            self.assertEqual(model["elements"]["demo.tool"]["title"], "Tool")
            output = Path(temporary) / "site"
            started = perf_counter()
            problems = render_repository(root, "草稿", output)
            elapsed = perf_counter() - started
            print(f"\nrender 单仓草稿用时：{elapsed:.3f} 秒")
            self.assertEqual(problems, [], problems)
            self.assertTrue((output / "DRAFT").is_file())
            self.assertTrue((output / "index.html").is_file())
            map_data, page_data = assert_page_matches_map(self, output, "草稿")
            self.assertEqual(set(map_data["nodes"]), set(model["elements"]))
            self.assertIn("elk.bundled.js", (output / "index.html").read_text(encoding="utf-8"))
            self.assertEqual(set(page_data["nodes"]), set(map_data["nodes"]))


if __name__ == "__main__":
    unittest.main()
