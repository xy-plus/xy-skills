"""Real LikeC4 CLI integration; failure to run npx is a test failure."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.checker import check_repository
from repo_map_lib.constants import STANDARD_KINDS
from repo_map_lib.render_command import render_repository


class LikeC4IntegrationTest(unittest.TestCase):
    def test_real_cli_validates_exports_and_builds_a_hash_routed_draft(self):
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

            problems, model = check_repository(root, False)
            self.assertEqual(problems, [], problems)
            self.assertEqual(model["elements"]["demo.tool"]["title"], "Tool")
            output = Path(temporary) / "site"
            problems = render_repository(root, "草稿", output)
            self.assertEqual(problems, [], problems)
            self.assertTrue((output / "DRAFT").is_file())
            self.assertTrue((output / "index.html").is_file())

    def test_real_cli_combines_parent_and_nested_repository_models(self):
        with tempfile.TemporaryDirectory(prefix="xy-repo-map-likec4-tree-") as temporary:
            parent = Path(temporary) / "workspace"
            parent.mkdir()
            subprocess.run(["git", "init", str(parent)], check=True, capture_output=True)
            (parent / ".gitignore").write_text("vendor/\n", encoding="utf-8")
            (parent / "README.md").write_text("Workspace docs.\n", encoding="utf-8")
            child = parent / "vendor"
            child.mkdir()
            subprocess.run(["git", "init", str(child)], check=True, capture_output=True)
            (child / "src").mkdir()
            (child / "src" / "engine.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(parent), "add", ".gitignore", "README.md"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(child), "add", "src"],
                           check=True, capture_output=True)

            parent_map = parent / "repo-map"
            child_map = child / "repo-map"
            for repo_map, root_id in ((parent_map, "workspace"), (child_map, "engine")):
                repo_map.mkdir()
                (repo_map / "likec4.config.json").write_text(
                    f'{{"name":"{root_id}"}}\n', encoding="utf-8")
                (repo_map / "kinds.c4").write_text(STANDARD_KINDS, encoding="utf-8")
                (repo_map / "approvals.txt").write_text("", encoding="utf-8")
            (parent_map / "model.c4").write_text(
                "model {\n  workspace = node 'Workspace' {\n"
                "    description 'Combines research and execution.'\n"
                "    link ../README.md 'docs'\n    link ../.gitignore 'data'\n"
                "    engine = repo 'Engine' {\n      description 'Execution subrepository.'\n"
                "      link ../vendor/ 'repo'\n    }\n  }\n}\n", encoding="utf-8")
            (child_map / "model.c4").write_text(
                "model {\n  engine = node 'Engine' {\n    description 'Runs orders.'\n"
                "    cache = node 'Cache' {\n      description 'Keeps market data.'\n"
                "      link ../src/engine.py 'code'\n    }\n  }\n}\n", encoding="utf-8")

            problems, parent_model = check_repository(parent, False)
            self.assertEqual(problems, [], problems)
            self.assertIn("workspace.engine", parent_model["elements"])
            output = Path(temporary) / "site"
            problems = render_repository(parent, "草稿", output)
            self.assertEqual(problems, [], problems)
            self.assertTrue((output / "DRAFT").is_file())
            self.assertTrue((output / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()
