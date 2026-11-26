import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "xy-repo-map"))
from repo_map_lib.validate_feature_list.cycle_imports import (  # noqa: E402
    cycle_registry_change_errors,
)


MISSING = object()


def feature_map(
    cycle_imports=MISSING,
    *,
    alpha=("alpha", "alpha"),
    beta=("beta", "beta"),
    title="Root",
):
    result = {
        "id": "root",
        "directory": "",
        "title": title,
        "children": [
            {"id": alpha[0], "directory": alpha[1], "children": []},
            {"id": beta[0], "directory": beta[1], "children": []},
        ],
    }
    if cycle_imports is not MISSING:
        result["existing_cycle_imports"] = cycle_imports
    return result


class CycleRegistryChangeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = Path(temporary.name) / "repository"
        self.repository.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")

    def git(self, *arguments):
        return subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def write_map(self, document):
        path = self.repository / "repo-map" / "features.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_rejects_new_cycle_entries(self):
        self.write_map(
            feature_map([{"from": "alpha", "to": "beta"}])
        )
        base = self.commit("base")
        self.write_map(
            feature_map(
                [
                    {"from": "alpha", "to": "beta"},
                    {"from": "beta", "to": "alpha"},
                ]
            )
        )
        head = self.commit("add cycle entry")

        errors = cycle_registry_change_errors(self.repository, base, head)

        self.assertEqual(len(errors), 1)
        self.assertIn("beta → alpha", errors[0])
        self.assertIn("名单只能删不能加", errors[0])

    def test_maps_renamed_ids_to_base_features_by_directory(self):
        self.write_map(
            feature_map(
                [{"from": "old-alpha", "to": "old-beta"}],
                alpha=("old-alpha", "alpha"),
                beta=("old-beta", "beta"),
            )
        )
        base = self.commit("base")
        self.write_map(
            feature_map(
                [{"from": "alpha", "to": "beta"}],
                alpha=("alpha", "alpha"),
                beta=("beta", "beta"),
            )
        )
        head = self.commit("rename ids")

        errors = cycle_registry_change_errors(self.repository, base, head)

        self.assertEqual(errors, [])

    def test_rejects_entries_when_both_feature_id_and_directory_change(self):
        self.write_map(
            feature_map([{"from": "alpha", "to": "beta"}])
        )
        base = self.commit("base")
        self.write_map(
            feature_map(
                [{"from": "new-alpha", "to": "new-beta"}],
                alpha=("new-alpha", "new-alpha"),
                beta=("new-beta", "new-beta"),
            )
        )
        head = self.commit("rename ids and directories")

        errors = cycle_registry_change_errors(self.repository, base, head)

        self.assertEqual(len(errors), 1)
        self.assertTrue(all("名单只能删不能加" in error for error in errors))

    def test_allows_deleting_cycle_entries(self):
        self.write_map(
            feature_map(
                [
                    {"from": "alpha", "to": "beta"},
                    {"from": "beta", "to": "alpha"},
                ]
            )
        )
        base = self.commit("base")
        self.write_map(feature_map([{ "from": "alpha", "to": "beta" }]))
        head = self.commit("remove cycle entry")

        errors = cycle_registry_change_errors(self.repository, base, head)

        self.assertEqual(errors, [])

    def test_allows_first_empty_registry_alongside_other_changes(self):
        self.write_map(feature_map())
        base = self.commit("base")
        self.write_map(feature_map(title="Updated root"))
        self.commit("other change")
        self.write_map(feature_map([], title="Updated root"))
        head = self.commit("first empty registry")

        errors = cycle_registry_change_errors(self.repository, base, head)

        self.assertEqual(errors, [])

    def test_allows_nonempty_first_registry_in_one_field_only_commit(self):
        self.write_map(feature_map())
        base = self.commit("base")
        self.write_map(
            feature_map([{"from": "alpha", "to": "beta"}])
        )
        head = self.commit("first registry")

        errors = cycle_registry_change_errors(self.repository, base, head)

        self.assertEqual(errors, [])

    def test_rejects_nonempty_first_registry_with_other_changes_or_multiple_commits(self):
        self.write_map(feature_map())
        base = self.commit("base")
        self.write_map(feature_map(title="Changed root"))
        self.write_map(
            feature_map(
                [{"from": "alpha", "to": "beta"}], title="Changed root"
            )
        )
        head = self.commit("first registry with other changes")

        errors = cycle_registry_change_errors(self.repository, base, head)
        self.assertEqual(len(errors), 1)
        self.assertIn("单独一个提交", errors[0])

    def test_rejects_nonempty_first_registry_after_multiple_commits(self):
        self.write_map(feature_map())
        base = self.commit("base")
        self.git("commit", "--allow-empty", "--quiet", "-m", "intermediate commit")
        self.write_map(feature_map([{"from": "alpha", "to": "beta"}]))
        head = self.commit("first registry")

        errors = cycle_registry_change_errors(self.repository, base, head)

        self.assertEqual(len(errors), 1)
        self.assertIn("单独一个提交", errors[0])

    def test_does_not_intercept_repositories_without_either_registry(self):
        self.write_map(feature_map())
        base = self.commit("base")
        self.write_map(feature_map(title="Updated root"))
        head = self.commit("change without registry")

        errors = cycle_registry_change_errors(self.repository, base, head)

        self.assertEqual(errors, [])
