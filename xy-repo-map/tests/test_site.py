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

    def test_page_has_no_external_network_references(self):
        html = render_html(self.page_data)
        self.assertIsNone(re.search(
            r"\b(?:src|href)\s*=\s*['\"]\s*(?:https?:)?//", html, re.IGNORECASE))
        self.assertNotRegex(html, r"\b(?:fetch|XMLHttpRequest|WebSocket)\s*\(")

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

    def test_html_template_stays_within_the_repository_file_length_limit(self):
        template = (Path(__file__).resolve().parents[1] / "repo_map_lib" / "templates"
                    / "index.html")
        self.assertLessEqual(len(template.read_text(encoding="utf-8").splitlines()), 500)

    def test_layout_waits_for_fonts_and_sizes_each_metric_from_canvas_measurements(self):
        template = (Path(__file__).resolve().parents[1] / "repo_map_lib" / "templates"
                    / "index.html").read_text(encoding="utf-8")
        self.assertIn("document.fonts.ready", template)
        self.assertIn("measure.measureText(String(text))", template)
        self.assertIn("metricWidths", template)
        self.assertNotIn("metricArea/4", template)

    def test_descriptions_keep_closing_punctuation_with_the_previous_character(self):
        template = (Path(__file__).resolve().parents[1] / "repo_map_lib" / "templates"
                    / "index.html").read_text(encoding="utf-8")
        self.assertIn("LEADING_PUNCTUATION", template)
        self.assertIn("wrapText", template)

    def test_page_layout_has_compact_unconnected_packing_and_a_scale_cap(self):
        template = (Path(__file__).resolve().parents[1] / "repo_map_lib" / "templates"
                    / "index.html").read_text(encoding="utf-8")
        self.assertIn('"elk.algorithm":"rectpacking"', template)
        self.assertIn('"elk.aspectRatio":"1.6"', template)
        self.assertIn("Math.min((width-padding*2)/currentBounds.width,", template)
        self.assertIn("currentBounds.height,1)", template)

    def test_connected_layout_wraps_to_a_landscape_shape_and_keeps_leaf_labels_readable(self):
        template = (Path(__file__).resolve().parents[1] / "repo_map_lib" / "templates"
                    / "index.html").read_text(encoding="utf-8")
        self.assertIn('"elk.layered.wrapping.strategy":"MULTI_EDGE"', template)
        self.assertIn(".title-depth-2{font-size:15px;font-weight:600}", template)
        self.assertIn(".metric-text{font-size:15px", template)
        self.assertIn("const METRIC_FONT=`600 15px ${FONT_FAMILY}`", template)
        self.assertIn("containerTop:height-2", template)

    def test_page_has_local_favicon_and_depth_title_styles(self):
        template = (Path(__file__).resolve().parents[1] / "repo_map_lib" / "templates"
                    / "index.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="icon" href="data:,">', template)
        self.assertIn("title-depth-0", template)
        self.assertIn("title-depth-1", template)
        self.assertIn("repo-badge", template)
        self.assertRegex(template, r"\.edge-label\{[^}]*font-size:1[1-9]px")

    def test_skill_is_the_only_map_data_contract_document(self):
        skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("map-data.json 格式", skill)
        for field in ('`mode`', '`nodes`', '`title`', '`kind`', '`parent`', '`own`', '`total`'):
            self.assertIn(field, skill)
        self.assertIn("`total`", skill)
        self.assertIn("子仓根", skill)


if __name__ == "__main__":
    unittest.main()
