"""Test approval records used to select features for cleanup review."""
from pathlib import Path
import subprocess
import sys
import unittest
import json
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "xy-cleanup"))
sys.path.insert(0, str(ROOT / "xy-repo-map" / "tests"))

import approvals  # noqa: E402
from map_fixture import build_map, feature, make_repo  # noqa: E402


COMMAND = ROOT / "xy-cleanup" / "approvals.py"


class ApprovalPlanTest(unittest.TestCase):
    def setUp(self):
        self.repository = make_repo(self, {
            "README.md": "Demo.\n",
            "alpha/main.py": "value = 1\n",
            "beta/main.py": "value = 2\n",
        }, feature("root", "", children=[
            feature("alpha", "alpha"),
            feature("beta", "beta"),
        ]))

    def run_approvals(self, *arguments):
        return subprocess.run(
            ["python3", "-B", str(COMMAND), *arguments],
            capture_output=True, text=True, check=False,
        )

    def plan(self, scope=("全部",), review="变过的"):
        return self.run_approvals(
            "plan", "--repo", str(self.repository), "--scope", *scope,
            "--review", review,
        )

    def record(self, scope=("全部",)):
        return self.run_approvals(
            "record", "--repo", str(self.repository), "--scope", *scope,
        )

    def link_approved_to_external_file(self):
        approved_path = self.repository / "repo-map" / "approved.json"
        external_path = self.repository.parent / "outside-approved.json"
        external_path.write_text("{}\n", encoding="utf-8")
        approved_path.symlink_to(external_path)
        return approved_path, external_path

    def link_repo_map_to_external_directory(self):
        repo_map_path = self.repository / "repo-map"
        external_directory = self.repository.parent / "outside-repo-map"
        external_directory.mkdir()
        (external_directory / "features.json").write_bytes(
            (repo_map_path / "features.json").read_bytes()
        )
        (repo_map_path / "features.json").unlink()
        repo_map_path.rmdir()
        repo_map_path.symlink_to(external_directory, target_is_directory=True)
        return external_directory

    def test_scope_validation_is_public_and_keeps_current_errors(self):
        data = build_map(self, self.repository)
        invalid_scopes = (
            (("全部", "alpha"), "--scope 使用 全部 时不能再列功能 id"),
            (("alpha", "alpha"), "--scope 不能重复列出功能 id"),
            (("missing",), "--scope 中有未知功能 id：missing"),
        )
        for scope, message in invalid_scopes:
            with self.subTest(scope=scope):
                with self.assertRaises(approvals.ArgumentError) as context:
                    approvals.validate_scope(data, scope)
                self.assertEqual(str(context.exception), message)

    def test_plan_lists_unrecorded_feature_in_scope(self):
        result = self.run_approvals(
            "plan", "--repo", str(self.repository), "--scope", "alpha",
            "--review", "变过的",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            "alpha\t没有认可记录\n要审 1 个功能（范围内 1 个）\n",
        )

    def test_record_writes_sorted_fingerprints_for_the_scope(self):
        result = self.record()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        approved_path = self.repository / "repo-map" / "approved.json"
        self.assertEqual(
            result.stdout,
            f"已记录 3 个功能：{approved_path}\n",
        )
        self.assertEqual(
            [line.split(":", 1)[0].strip(' "')
             for line in approved_path.read_text(encoding="utf-8").splitlines()[1:-1]],
            ["alpha", "beta", "root"],
        )
        self.assertEqual(
            sorted(path.name for path in approved_path.parent.iterdir()),
            ["approved.json", "features.json"],
        )
        plan = self.plan()
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertEqual(plan.stdout, "范围内没有变过的功能（范围内 3 个）\n")

    def test_plan_rejects_approved_symlink_without_reading_external_file(self):
        approved_path, external_path = self.link_approved_to_external_file()
        original_content = external_path.read_text(encoding="utf-8")

        result = self.plan()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("认可记录", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertTrue(approved_path.is_symlink())
        self.assertEqual(external_path.read_text(encoding="utf-8"), original_content)

    def test_record_rejects_approved_symlink_without_writing_external_file(self):
        approved_path, external_path = self.link_approved_to_external_file()
        original_content = external_path.read_text(encoding="utf-8")

        result = self.record()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("认可记录", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertTrue(approved_path.is_symlink())
        self.assertEqual(external_path.read_text(encoding="utf-8"), original_content)

    def test_plan_rejects_repo_map_directory_symlink_without_creating_files(self):
        external_directory = self.link_repo_map_to_external_directory()

        result = self.plan()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("认可记录", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            sorted(path.name for path in external_directory.iterdir()),
            ["features.json"],
        )

    def test_record_rejects_repo_map_directory_symlink_without_creating_files(self):
        external_directory = self.link_repo_map_to_external_directory()

        result = self.record()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("认可记录", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            sorted(path.name for path in external_directory.iterdir()),
            ["features.json"],
        )

    def test_record_removes_temporary_file_when_replace_fails(self):
        data = build_map(self, self.repository)
        values, _ = approvals.feature_values(self.repository)
        fingerprints = approvals.feature_fingerprints(self.repository, data, values)
        approved_directory = self.repository / "repo-map"

        with mock.patch.object(approvals.os, "replace", side_effect=OSError("替换失败")):
            with self.assertRaisesRegex(OSError, "替换失败"):
                approvals._record(
                    self.repository, data, values, fingerprints, {}, ["alpha"]
                )

        self.assertEqual(
            sorted(path.name for path in approved_directory.iterdir()),
            ["features.json"],
        )

    def test_plan_expands_parent_scope_and_can_review_every_feature(self):
        changed = self.plan(("root",))
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        self.assertEqual(
            changed.stdout,
            "root\t没有认可记录\nalpha\t没有认可记录\n"
            "beta\t没有认可记录\n要审 3 个功能（范围内 3 个）\n",
        )

        all_features = self.plan(("root",), "全部")
        self.assertEqual(all_features.returncode, 0, all_features.stdout + all_features.stderr)
        self.assertEqual(
            all_features.stdout,
            "root\t全部重审\nalpha\t全部重审\nbeta\t全部重审\n"
            "要审 3 个功能（范围内 3 个）\n",
        )

    def test_plan_detects_only_a_changed_feature_file(self):
        recorded = self.record()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

        (self.repository / "alpha" / "main.py").write_text("value = 99\n", encoding="utf-8")
        result = self.plan()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            "alpha\t认可后变过\n要审 1 个功能（范围内 3 个）\n",
        )

    def test_plan_tracks_each_manifest_entry_without_crossing_to_siblings(self):
        recorded = self.record()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        manifest_path = self.repository / "repo-map" / "features.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["children"][1]["title"] = "Changed beta title"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        alpha = self.plan(("alpha",))
        self.assertEqual(alpha.returncode, 0, alpha.stdout + alpha.stderr)
        self.assertEqual(alpha.stdout, "范围内没有变过的功能（范围内 1 个）\n")

        beta = self.plan(("beta",))
        self.assertEqual(beta.returncode, 0, beta.stdout + beta.stderr)
        self.assertEqual(
            beta.stdout,
            "beta\t认可后变过\n要审 1 个功能（范围内 1 个）\n",
        )

    def test_plan_ignores_approved_file_formatting_changes(self):
        recorded = self.record()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        approved_path = self.repository / "repo-map" / "approved.json"
        subprocess.run(
            ["git", "-C", str(self.repository), "add", "repo-map/approved.json"],
            check=True,
        )
        values = json.loads(approved_path.read_text(encoding="utf-8"))
        approved_path.write_text(json.dumps(values, indent=4) + "\n", encoding="utf-8")

        result = self.plan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "范围内没有变过的功能（范围内 3 个）\n")

    def test_plan_hashes_a_symlink_target_without_following_it(self):
        link_path = self.repository / "alpha" / "pointer"
        link_path.symlink_to("first-target")
        subprocess.run(["git", "-C", str(self.repository), "add", "-A"], check=True)
        recorded = self.record()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

        link_path.unlink()
        link_path.symlink_to("second-target")
        result = self.plan(("alpha",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            "alpha\t认可后变过\n要审 1 个功能（范围内 1 个）\n",
        )

    def test_record_rejects_drafts_without_writing(self):
        temporary_repo = make_repo(self, {
            "README.md": "Demo.\n",
            "alpha/main.py": "value = 1\n",
        }, feature("root", "", children=[
            feature("alpha", "alpha", source="起草"),
        ]))
        result = self.run_approvals(
            "record", "--repo", str(temporary_repo), "--scope", "alpha",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertFalse((temporary_repo / "repo-map" / "approved.json").exists())

    def test_record_preserves_other_current_records_and_drops_unknown_ids(self):
        recorded = self.record()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        approved_path = self.repository / "repo-map" / "approved.json"
        values = json.loads(approved_path.read_text(encoding="utf-8"))
        values["removed-feature"] = "f" * 64
        approved_path.write_text(json.dumps(values), encoding="utf-8")

        result = self.record(("alpha",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, f"已记录 1 个功能：{approved_path}\n")
        saved = json.loads(approved_path.read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"alpha", "beta", "root"})

    def test_both_commands_reject_invalid_approval_records(self):
        approved_path = self.repository / "repo-map" / "approved.json"
        invalid_records = (
            "[]",
            '{"alpha": 1}',
            '{"alpha": null}',
            '{"alpha": "' + "A" * 64 + '"}',
            '{"alpha": "' + "a" * 64 + '", "alpha": "' + "b" * 64 + '"}',
        )
        for document in invalid_records:
            with self.subTest(document=document[:40]):
                approved_path.write_text(document, encoding="utf-8")
                for command in ("plan", "record"):
                    if command == "plan":
                        result = self.plan(("alpha",))
                    else:
                        result = self.record(("alpha",))
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("认可记录", result.stdout)
                    self.assertEqual(result.stderr, "")

    def test_scope_arguments_reject_mixed_all_duplicates_and_unknown_ids(self):
        for arguments in (
            ("plan", "--repo", str(self.repository), "--scope", "全部", "alpha",
             "--review", "变过的"),
            ("record", "--repo", str(self.repository), "--scope", "alpha", "alpha"),
            ("plan", "--repo", str(self.repository), "--scope", "missing",
             "--review", "变过的"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_approvals(*arguments)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
