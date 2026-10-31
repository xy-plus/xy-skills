"""Behavior tests for graph reference reconciliation."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS.parents[1] / "xy-repo-map"))

from references import build_reference_report
from references import main as references_main
from references import module_names
from references import node_names
from references import text_lines
from check_audit import repository_context
from repo_map_lib.git_repository import tracked_paths
from repo_map_lib.ownership import Owner
from test_data.create_fixture import create_fixture_repo


class ReferencesTest(unittest.TestCase):
    def test_initialized_submodule_is_not_read_as_a_text_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "parent"
            submodule_source = base / "submodule-source"
            repository.mkdir()
            submodule_source.mkdir()
            for root in (repository, submodule_source):
                subprocess.run(["git", "init", "--quiet", str(root)], check=True,
                               capture_output=True)
                subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"],
                               check=True, capture_output=True)
                subprocess.run(["git", "-C", str(root), "config", "user.email",
                                "fixture@example.invalid"], check=True, capture_output=True)
            (submodule_source / "README.md").write_text("submodule\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(submodule_source), "add", "README.md"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(submodule_source), "commit", "--quiet",
                            "-m", "submodule source"], check=True, capture_output=True)
            (repository / "app.py").write_text("# parent source\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "app.py"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "commit", "--quiet",
                            "-m", "parent source"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "config", "protocol.file.allow",
                            "always"], check=True, capture_output=True)
            subprocess.run(["git", "-c", "protocol.file.allow=always", "-C",
                            str(repository), "submodule", "add",
                            str(submodule_source), "vendor"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "commit", "--quiet", "-m",
                            "add submodule"], check=True, capture_output=True)

            tracked = tracked_paths(repository)
            self.assertIn("vendor", tracked)
            self.assertTrue((repository / "vendor" / "README.md").is_file())
            model = {"elements": {"app": {}, "app.vendor": {}}, "relations": {}}
            owners = {
                "app.py": Owner("app", "code"),
                "vendor": Owner("app.vendor", "repo"),
            }
            with patch("references.text_lines", wraps=text_lines) as scan:
                result = build_reference_report(repository, model, tracked, owners)
            self.assertEqual(result["mentions"], [])
            self.assertEqual(result["skipped_files"], [])
            scan.assert_called_once_with(repository / "app.py")

    def test_fixture_module_imports_match_both_graph_relationships(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = create_fixture_repo(Path(temporary) / "review-target")
            root, model, tracked, owners = repository_context(repository)
            result = build_reference_report(root, model, tracked, owners)
            evidence = {
                (mention["from"], mention["to"]): mention["evidence"]
                for mention in result["mentions"]
            }
            for relation, file_path, line_number in [
                (("cleanup_fixture.cli", "cleanup_fixture.core"),
                 "src/cleanup_fixture/cli.py", 5),
                (("cleanup_fixture.tests", "cleanup_fixture.core"),
                 "tests/test_core.py", 4),
            ]:
                self.assertIn(relation, evidence)
                matching_evidence = [
                    mention for mention in evidence[relation]
                    if (mention["file"], mention["line"]) == (file_path, line_number)
                ]
                self.assertEqual(len(matching_evidence), 1)
                mention = matching_evidence[0]
                self.assertIn("cleanup_fixture.core", mention["matches"])
            self.assertEqual(result["relationships_without_reference"], [])

    def test_rust_module_names_include_suffix_and_crate_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src" / "cache").mkdir(parents=True)
            (root / "src" / "cache" / "store.rs").write_text(
                "pub struct Store;\n", encoding="utf-8"
            )
            (root / "src" / "main.rs").write_text(
                "use crate::cache::store::Store;\n", encoding="utf-8"
            )
            model = {
                "elements": {"app": {}, "app.cache": {}},
                "relations": {"app-cache": {
                    "source": {"model": "app"},
                    "target": {"model": "app.cache"}, "title": "uses"
                }},
            }
            tracked = ["src/cache/store.rs", "src/main.rs"]
            owners = {
                "src/cache/store.rs": Owner("app.cache", "code"),
                "src/main.rs": Owner("app", "code"),
            }
            names = node_names(model, tracked, owners)
            self.assertIn("cache::store", names["app.cache"])
            self.assertIn("crate::cache::store", names["app.cache"])
            result = build_reference_report(root, model, tracked, owners)
            mention = next(item for item in result["mentions"]
                           if item["from"] == "app" and item["to"] == "app.cache")
            self.assertEqual(mention["evidence"][0]["file"], "src/main.rs")
            self.assertEqual(mention["evidence"][0]["line"], 1)
            self.assertIn("crate::cache::store", mention["evidence"][0]["matches"])

        cache_names = node_names(
            {"elements": {"cache": {}}}, ["src/cache.rs"],
            {"src/cache.rs": Owner("cache", "code")}
        )
        self.assertIn("crate::cache", cache_names["cache"])

    def test_rust_crate_entry_files_do_not_become_module_names(self):
        self.assertEqual(module_names("src/cache/mod.rs"), {"cache", "crate::cache"})
        self.assertEqual(module_names("src/lib.rs"), set())
        self.assertEqual(module_names("src/main.rs"), set())

    def test_python_module_names_use_dotted_suffixes_and_strip_init(self):
        model = {"elements": {"package": {}}, "relations": {}}
        tracked = ["src/cleanup_fixture/core.py", "src/cleanup_fixture/__init__.py"]
        owners = {path: Owner("package", "code") for path in tracked}
        names = node_names(model, tracked, owners)
        self.assertIn("src.cleanup_fixture.core", names["package"])
        self.assertIn("cleanup_fixture.core", names["package"])
        self.assertIn("src.cleanup_fixture", names["package"])
        self.assertNotIn("__init__", names["package"])

    def test_single_segment_module_name_is_only_added_when_unique(self):
        model = {"elements": {"alpha": {}, "beta": {}, "unique": {}}, "relations": {}}
        tracked = ["src/alpha/engine.py", "src/beta/engine.py", "src/unique.py"]
        owners = {
            "src/alpha/engine.py": Owner("alpha", "code"),
            "src/beta/engine.py": Owner("beta", "code"),
            "src/unique.py": Owner("unique", "code"),
        }
        names = node_names(model, tracked, owners)
        self.assertNotIn("engine", names["alpha"])
        self.assertNotIn("engine", names["beta"])
        self.assertIn("unique", names["unique"])

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
                "workflow.py": Owner("skills", "code"),
                "xy-runs/runner.py": Owner("skills.runs", "code"),
                "other.py": Owner("skills.other", "code"),
            }
            (root / "other.py").write_text("# uses workflow.py\n", encoding="utf-8")
            result = build_reference_report(root, model, tracked, owners)
            self.assertEqual(result["mentions"], [
                {"from": "skills", "to": "skills.runs", "evidence": [
                    {"file": "workflow.py", "line": 1, "matches": ["xy-runs"]}
                ]},
                {"from": "skills.other", "to": "skills", "evidence": [
                    {"file": "other.py", "line": 1,
                     "matches": ["workflow", "workflow.py"]}
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
                {"workflow.py": Owner("skills.workflow", "code"),
                 "xy-runs/runner.py": Owner("skills.runs", "code")}
            )
            self.assertEqual(result["mentions"][0]["to"], "skills.runs")
            self.assertEqual(result["mentions"][0]["evidence"][0]["matches"], ["xy-runs"])

    def test_reports_graph_edges_without_any_text_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "alpha.py").write_text("def alpha(): pass\n", encoding="utf-8")
            (root / "beta.py").write_text("def beta(): pass\n", encoding="utf-8")
            model = {
                "elements": {"alpha": {}, "beta": {}},
                "relations": {"alpha-beta": {
                    "source": {"model": "alpha"},
                    "target": {"model": "beta"}, "title": "uses"
                }},
            }
            result = build_reference_report(
                root, model, ["alpha.py", "beta.py"],
                {"alpha.py": Owner("alpha", "code"), "beta.py": Owner("beta", "code")}
            )
            self.assertEqual(result["mentions"], [])
            self.assertEqual(result["relationships_without_reference"], [
                {"from": "alpha", "to": "beta", "title": "uses"}
            ])

    def test_skips_binary_files_and_does_not_count_self_mentions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "alpha.py").write_text("# alpha.py self name\n", encoding="utf-8")
            (root / "beta.bin").write_bytes(b"alpha.py\x00beta.py")
            model = {
                "elements": {
                    "alpha": {"links": [{"url": "../alpha.py", "title": "code"}]},
                    "beta": {"links": [{"url": "../beta.bin", "title": "data"}]},
                },
                "relations": {},
            }
            result = build_reference_report(
                root, model, ["alpha.py", "beta.bin"],
                {"alpha.py": Owner("alpha", "code"), "beta.bin": Owner("beta", "data")}
            )
            self.assertEqual(result["mentions"], [])
            self.assertEqual(result["skipped_files"], [
                {"file": "beta.bin", "reason": "含 NUL 字节"}
            ])

    def test_reports_non_utf8_files_as_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "invalid.txt").write_bytes(b"\xff\xfe")
            model = {"elements": {"app": {}}, "relations": {}}
            result = build_reference_report(
                root, model, ["invalid.txt"],
                {"invalid.txt": Owner("app", "docs")}
            )
        self.assertEqual(result["mentions"], [])
        self.assertEqual(result["skipped_files"], [
            {"file": "invalid.txt", "reason": "非 UTF-8 文件"}
        ])

    def test_file_read_oserror_propagates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "source.py"
            path.write_text("pass\n", encoding="utf-8")
            model = {"elements": {"app": {}}, "relations": {}}
            from unittest.mock import patch
            with patch("references.Path.read_bytes", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(PermissionError, "denied"):
                    build_reference_report(
                        root, model, ["source.py"],
                        {"source.py": Owner("app", "code")}
                    )

    def test_file_read_oserror_makes_command_exit_one(self):
        import contextlib
        import io
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph = {"elements": {"app": {}}, "relations": {}}
            owners = {"source.py": Owner("app", "code")}
            output = io.StringIO()
            with patch("references.repository_context",
                       return_value=(root, graph, ["source.py"], owners)), \
                    patch("references.text_lines", side_effect=PermissionError("denied")), \
                    contextlib.redirect_stderr(output):
                status = references_main(["--repo", str(root)])
        self.assertEqual(status, 1)
        self.assertIn("denied", output.getvalue())

    def test_repeated_directory_basename_is_not_used_as_a_node_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tracked = [
                "alpha/tests/test_alpha.py",
                "beta/tests/test_beta.py",
                "workflow.py",
            ]
            for file_path in tracked:
                path = root / file_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# tests are part of pipeline\n", encoding="utf-8")
            model = {
                "elements": {
                    "app": {"links": [{"url": "../workflow.py", "title": "code"}]},
                    "app.alpha": {"links": [{
                        "url": "../alpha/tests/test_alpha.py", "title": "tests"
                    }]},
                    "app.beta": {"links": [{
                        "url": "../beta/tests/test_beta.py", "title": "tests"
                    }]},
                },
                "relations": {},
            }
            owners = {
                "alpha/tests/test_alpha.py": Owner("app.alpha", "tests"),
                "beta/tests/test_beta.py": Owner("app.beta", "tests"),
                "workflow.py": Owner("app", "code"),
            }
            names = node_names(model, tracked, owners)
            self.assertNotIn("tests", names["app.alpha"])
            self.assertNotIn("tests", names["app.beta"])
            result = build_reference_report(root, model, tracked, owners)
            self.assertEqual(result["mentions"], [])


if __name__ == "__main__":
    unittest.main()
