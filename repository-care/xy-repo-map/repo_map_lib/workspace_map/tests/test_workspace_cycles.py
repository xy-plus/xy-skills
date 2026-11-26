import json
from pathlib import Path
import subprocess
import sys
import unittest

REPOSITORY_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "tests"))
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "xy-repo-map" / "repo_map_lib" / "workspace_map" / "tests"))
import test_workspace as workspace_test  # noqa: E402
from map_fixture import feature, make_workspace  # noqa: E402


class WorkspaceCycleTest(unittest.TestCase):
    def setUp(self):
        dependencies = ("sub-b", "missing-tool")
        self.workspace = make_workspace(self, dependencies, ())
        self.output = self.workspace.parent / "rendered"

    def test_render_prefixes_child_cycle_with_feature_ids(self):
        child = self.workspace / "sub-a"
        manifest = child / "repo-map" / "features.json"
        root = json.loads(manifest.read_text(encoding="utf-8"))
        root["children"][0].update(directory="lib/part", items=[], children=[])
        root["children"].append(
            feature("peer", "lib/peer", source="原话", children=[], items=[])
        )
        root["existing_cycle_imports"] = [
            {"from": "part", "to": "peer"},
            {"from": "peer", "to": "part"},
        ]
        manifest.write_text(json.dumps(root, ensure_ascii=False), encoding="utf-8")
        (child / "lib" / "main.py").unlink()
        for feature_id, target_id in (("part", "peer"), ("peer", "part")):
            source = child / "lib" / feature_id / "main.py"
            source.parent.mkdir(parents=True)
            source.write_text(f"import {target_id}.main\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(child), "add", "-A"], check=True)
        result = workspace_test.WorkspaceRenderTest.render(self)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads((self.output / "map-data.json").read_text(encoding="utf-8"))
        self.assertEqual(data["features"]["sub-a/part"]["cycle_with"], ["sub-a/peer"])
        self.assertEqual(data["features"]["sub-a/peer"]["cycle_with"], ["sub-a/part"])
