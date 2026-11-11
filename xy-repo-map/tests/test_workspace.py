import json
import shutil
import subprocess
import unittest

from map_fixture import COMMAND, feature, make_workspace


class WorkspaceRenderTest(unittest.TestCase):
    def setUp(self):
        self.workspace = make_workspace(self)
        self.output = self.workspace.parent / "rendered"

    def render(self):
        return subprocess.run(
            ["python3", "-B", str(COMMAND), "render-workspace",
             "--repo", str(self.workspace), "--out", str(self.output)],
            capture_output=True, text=True, check=False,
        )

    def test_render_workspace_combines_maps_and_resolves_external_names(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads((self.output / "map-data.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(data["features"]),
            {"root", "sub-a/root", "sub-a/part", "sub-b/root", "sub-b/part"},
        )
        self.assertEqual(data["features"]["root"]["children"], ["sub-a/root", "sub-b/root"])
        self.assertEqual(data["features"]["sub-a/root"]["parent"], "root")
        self.assertEqual(data["features"]["sub-a/part"]["parent"], "sub-a/root")
        self.assertEqual(
            data["features"]["root"]["lines"]["total"],
            {"code": 2, "docs": 3, "comments": 0, "tests": 0},
        )
        sub_a_files = [
            path
            for group in data["features"]["sub-a/root"]["files"].values()
            for path in group
        ]
        self.assertIn("sub-a/README.md", sub_a_files)
        part_files = [
            path
            for group in data["features"]["sub-a/part"]["files"].values()
            for path in group
        ]
        self.assertIn("sub-a/lib/main.py", part_files)
        self.assertEqual(
            data["features"]["sub-a/root"]["uses"],
            [{"feature": "sub-b/root", "by": "mention"}],
        )
        self.assertEqual(data["features"]["sub-a/root"]["external_uses"], ["missing-tool"])
        self.assertEqual(data["features"]["sub-a/part"]["external_uses"], [])
        self.assertEqual(data["markers"], {
            "等你拍板": 0,
            "还没实现": 0,
            "没归到功能的代码或测试": 0,
        })

    def test_external_use_named_after_workspace_checkout_is_removed_without_root_relation(self):
        manifest_path = self.workspace / "sub-a" / "repo-map" / "features.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["external_dependencies"].append("workspace")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (self.workspace / "sub-a" / "README.md").write_text(
            "Uses sub-b missing-tool workspace.\n", encoding="utf-8",
        )

        result = self.render()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads((self.output / "map-data.json").read_text(encoding="utf-8"))
        feature = data["features"]["sub-a/root"]
        self.assertEqual(feature["external_uses"], ["missing-tool"])
        self.assertFalse(any(use["feature"] == "root" for use in feature["uses"]))

    def test_render_workspace_rejects_an_empty_subrepository_list(self):
        shutil.rmtree(self.workspace / "sub-a")
        shutil.rmtree(self.workspace / "sub-b")
        root = feature("root", "", subrepositories=[], external_dependencies=[])
        (self.workspace / "repo-map" / "features.json").write_text(
            json.dumps(root, ensure_ascii=False), encoding="utf-8",
        )

        result = self.render()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("没有登记子仓", result.stdout + result.stderr)
        self.assertFalse(self.output.exists())

    def test_render_workspace_reports_a_missing_child_checkout(self):
        shutil.rmtree(self.workspace / "sub-a")

        result = self.render()

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("sub-a", output)
        self.assertIn("不存在", output)
        self.assertIn("子仓所在的检出", output)
        self.assertFalse(self.output.exists())

    def test_render_workspace_reports_a_child_without_its_own_map(self):
        (self.workspace / "sub-a" / "repo-map" / "features.json").unlink()

        result = self.render()

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("sub-a", output)
        self.assertIn("repo-map/features.json", output)
        self.assertIn("起草", output)
        self.assertFalse(self.output.exists())

    def test_render_workspace_reports_an_invalid_child_map(self):
        (self.workspace / "sub-a" / "repo-map" / "features.json").write_text(
            "{}", encoding="utf-8",
        )

        result = self.render()

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("sub-a", output)
        self.assertIn("功能图检查失败", output)
        self.assertIn("缺少键", output)
        self.assertFalse(self.output.exists())

    def test_render_workspace_keeps_argument_errors_at_two(self):
        result = subprocess.run(
            ["python3", "-B", str(COMMAND), "render-workspace", "--repo",
             str(self.workspace)],
            capture_output=True, text=True, check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--out", result.stderr)

