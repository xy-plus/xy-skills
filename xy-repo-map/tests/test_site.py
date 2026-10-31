"""Offline HTML, publication files, and pinned elkjs asset tests."""
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.site import render_html, write_site


class SiteTest(unittest.TestCase):
    def setUp(self):
        self.page_data = {
            "mode": "草稿",
            "top_roots": ["workspace"],
            "nodes": {
                "workspace": {
                    "title": "工作区",
                    "kind": "node",
                    "parent": None,
                    "own": {"code": 0, "docs": 0, "comments": 0, "tests": 0},
                    "total": {"code": 0, "docs": 0, "comments": 0, "tests": 0},
                    "description": "根节点",
                    "coverage": "不适用",
                },
            },
            "relations": [],
            "repo_roots": {},
            "drill_views": {"workspace": {
                "nodes": ["workspace"],
                "relations": [],
                "breadcrumbs": ["workspace"],
            }},
        }
        self.map_data = {
            "mode": "草稿",
            "nodes": {
                "workspace": {
                    "title": "工作区",
                    "kind": "node",
                    "parent": None,
                    "own": {"code": 0, "docs": 0, "comments": 0, "tests": 0},
                    "total": {"code": 0, "docs": 0, "comments": 0, "tests": 0},
                },
            },
        }

    def test_embedded_data_round_trips_and_closing_script_is_escaped(self):
        payload = "literal </script><script>alert('x')</script>"
        self.page_data["nodes"]["workspace"]["description"] = payload
        html = render_html(self.page_data)
        match = re.search(r'<script id="page-data" type="application/json">(.*?)</script>',
                          html, re.DOTALL)
        self.assertIsNotNone(match)
        self.assertIn("<\\/script>", match.group(1))
        self.assertEqual(json.loads(match.group(1)), self.page_data)

    def test_mode_marker_text_in_node_data_is_not_replaced(self):
        self.page_data["mode"] = "正式"
        self.page_data["nodes"]["workspace"]["title"] = "__MODE_STATUS__"
        self.page_data["nodes"]["workspace"]["description"] = "说明 __MODE_STATUS__ 原样保留"
        html = render_html(self.page_data)
        match = re.search(r'<script id="page-data" type="application/json">(.*?)</script>',
                          html, re.DOTALL)
        self.assertIsNotNone(match)
        embedded = json.loads(match.group(1))
        self.assertEqual(embedded["nodes"]["workspace"]["title"], "__MODE_STATUS__")
        self.assertEqual(embedded["nodes"]["workspace"]["description"],
                         "说明 __MODE_STATUS__ 原样保留")

    def test_site_writes_matching_map_data_local_elk_and_mode_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "draft"
            destination.mkdir()
            write_site(self.map_data, self.page_data, destination)
            self.assertEqual(json.loads((destination / "map-data.json").read_text(encoding="utf-8")),
                             self.map_data)
            html = (destination / "index.html").read_text(encoding="utf-8")
            self.assertIn("草稿·未经同意", html)
            self.assertTrue((destination / "DRAFT").is_file())
            self.assertTrue((destination / "elk.bundled.js").is_file())
            self.assertTrue((destination / "map-page.js").is_file())

    def test_formal_site_has_no_draft_marker(self):
        formal_map = {**self.map_data, "mode": "正式"}
        formal_page = {**self.page_data, "mode": "正式"}
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "formal"
            destination.mkdir()
            write_site(formal_map, formal_page, destination)
            html = (destination / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("草稿·未经同意", html)
            self.assertFalse((destination / "DRAFT").exists())

    def test_bundled_elkjs_matches_the_approved_package_digest(self):
        asset = (Path(__file__).resolve().parents[1] / "third_party" / "elkjs-0.9.3"
                 / "elk.bundled.js")
        self.assertEqual(sha256(asset.read_bytes()).hexdigest(),
                         "b0745abd7f23cd91690a1587e377edbe19fd7233c783300290936720546216d4")

if __name__ == "__main__":
    unittest.main()
