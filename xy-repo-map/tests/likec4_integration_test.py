"""Real LikeC4 CLI integration; failure to run npx is a test failure."""
from pathlib import Path
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map import main
from repo_map_lib.checker import check_repository
from repo_map_lib.constants import STANDARD_KINDS
from repo_map_lib.likec4 import export_model
from repo_map_lib.render_command import render_repository
from repo_map_lib.structure import structure_digest


def run_cli(*arguments):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(["repo_map.py", *arguments])


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

    def test_real_cli_records_checks_and_renders_parent_with_single_node_child(self):
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
            extra_models = parent_map / "parts"
            extra_models.mkdir()
            (extra_models / "kinds.c4").write_text(
                "model {\n  extend workspace {\n"
                "    research = node 'Research' {\n      description 'Explores data.'\n"
                "    }\n  }\n}\n", encoding="utf-8")
            (parent_map / "model.c4").write_text(
                "model {\n  workspace = node 'Workspace' {\n"
                "    description 'Combines research and execution.'\n"
                "    link ../README.md 'docs'\n    link ../.gitignore 'data'\n"
                "    engine = repo 'Engine' {\n      description 'Execution subrepository.'\n"
                "      link ../vendor/ 'repo'\n    }\n  }\n}\n", encoding="utf-8")
            (child_map / "model.c4").write_text(
                "model {\n  engine = node 'Engine' {\n    description 'Runs orders.'\n"
                "    link ../src/engine.py 'code'\n  }\n}\n", encoding="utf-8")

            exported_models = [export_model(repo_map) for repo_map in (parent_map, child_map)]
            self.assertIn("workspace.research", exported_models[0]["elements"])
            digests = [structure_digest(model) for model in exported_models]
            questions = [
                {"question": f"请确认 repo-map sha256 {digest}",
                 "options": [{"label": "同意改图"}, {"label": "不同意"}]}
                for digest in digests
            ]
            answers = {question["question"]: "同意改图" for question in questions}
            quote = Path(temporary) / "xy-goal" / "quotes" / "57.md"
            quote.parent.mkdir(parents=True)
            quote.write_text(json.dumps({
                "tool_input": {"questions": questions, "answers": answers},
                "tool_response": {"questions": questions, "answers": dict(answers)},
            }, ensure_ascii=False), encoding="utf-8")

            self.assertEqual(run_cli("record", "--repo", str(parent), "--approval", str(quote)), 0)
            self.assertEqual(run_cli("record", "--repo", str(child), "--approval", str(quote)), 0)
            self.assertEqual(run_cli("check", "--repo", str(parent)), 0)

            draft = Path(temporary) / "draft-site"
            formal = Path(temporary) / "formal-site"
            self.assertEqual(run_cli("render", "--repo", str(parent), "--mode", "草稿",
                                     "--out", str(draft)), 0)
            self.assertTrue((draft / "DRAFT").is_file())
            self.assertTrue((draft / "index.html").is_file())
            self.assertEqual(run_cli("render", "--repo", str(parent), "--mode", "正式",
                                     "--out", str(formal)), 0)
            self.assertTrue((formal / "index.html").is_file())
            self.assertFalse((formal / "DRAFT").exists())


if __name__ == "__main__":
    unittest.main()
