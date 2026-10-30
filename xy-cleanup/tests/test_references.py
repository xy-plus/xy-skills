"""Behavior tests for graph reference reconciliation."""
from pathlib import Path
import sys
import tempfile
import unittest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))

from references import build_reference_report


class ReferencesTest(unittest.TestCase):
    def test_reports_file_mentions_against_missing_graph_edges(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "xy-runs").mkdir()
            (root / "xy-runs" / "runner.py").write_text(
                "def run():\n    return True\n", encoding="utf-8"
            )
            (root / "workflow.py").write_text(
                "# xy-runs is started before every task\nfrom xy_runs import run\n", encoding="utf-8"
            )
            model = {
                "elements": {
                    "skills": {"title": "Skills", "links": [
                        {"url": "../workflow.py", "title": "code"}
                    ]},
                    "skills.runs": {"title": "Runs", "links": [
                        {"url": "../xy-runs", "title": "code"}
                    ]},
                    "skills.other": {"title": "Other", "links": [
                        {"url": "../other.py", "title": "code"}
                    ]},
                },
                "relations": {},
            }
            tracked = ["workflow.py", "xy-runs/runner.py", "other.py"]
            owners = {
                "workflow.py": "skills",
                "xy-runs/runner.py": "skills.runs",
                "other.py": "skills.other",
            }
            (root / "other.py").write_text("# uses workflow.py\n", encoding="utf-8")
            result = build_reference_report(root, model, tracked, owners)
            self.assertEqual(result["mentions"], [
                {"from": "skills", "to": "skills.runs", "evidence": [
                    {"file": "workflow.py", "line": 1, "matches": ["xy-runs"]}
                ]},
                {"from": "skills.other", "to": "skills", "evidence": [
                    {"file": "other.py", "line": 1, "matches": ["workflow.py"]}
                ]},
            ])
            self.assertEqual(result["references_without_relationship"], result["mentions"])
            self.assertEqual(result["relationships_without_reference"], [])

    def test_exclusively_owned_directory_adds_its_path_and_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "xy-runs").mkdir()
            (root / "xy-runs" / "runner.py").write_text("def run(): pass\n", encoding="utf-8")
            (root / "workflow.py").write_text("# start xy-runs\n", encoding="utf-8")
            model = {
                "elements": {
                    "skills.workflow": {"links": [{"url": "../workflow.py", "title": "code"}]},
                    "skills.runs": {"links": [{"url": "../xy-runs/runner.py", "title": "code"}]},
                },
                "relations": {},
            }
            result = build_reference_report(
                root, model, ["workflow.py", "xy-runs/runner.py"],
                {"workflow.py": "skills.workflow", "xy-runs/runner.py": "skills.runs"}
            )
            self.assertEqual(result["mentions"][0]["to"], "skills.runs")
            self.assertEqual(result["mentions"][0]["evidence"][0]["matches"], ["xy-runs"])

    def test_reports_graph_edges_without_any_text_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.py").write_text("def a(): pass\n", encoding="utf-8")
            (root / "b.py").write_text("def b(): pass\n", encoding="utf-8")
            model = {
                "elements": {"a": {}, "b": {}},
                "relations": {"a-b": {
                    "source": {"model": "a"}, "target": {"model": "b"}, "title": "uses"
                }},
            }
            result = build_reference_report(
                root, model, ["a.py", "b.py"], {"a.py": "a", "b.py": "b"}
            )
            self.assertEqual(result["mentions"], [])
            self.assertEqual(result["relationships_without_reference"], [
                {"from": "a", "to": "b", "title": "uses"}
            ])

    def test_skips_binary_files_and_does_not_count_self_mentions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.py").write_text("# a.py self name\n", encoding="utf-8")
            (root / "b.bin").write_bytes(b"a.py\x00b.py")
            model = {
                "elements": {
                    "a": {"links": [{"url": "../a.py", "title": "code"}]},
                    "b": {"links": [{"url": "../b.bin", "title": "data"}]},
                },
                "relations": {},
            }
            result = build_reference_report(
                root, model, ["a.py", "b.bin"], {"a.py": "a", "b.bin": "b"}
            )
            self.assertEqual(result["mentions"], [])


if __name__ == "__main__":
    unittest.main()
