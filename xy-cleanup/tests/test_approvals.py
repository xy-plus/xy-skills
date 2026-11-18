from pathlib import Path
from contextlib import redirect_stdout
import io
import os
import subprocess
import sys
import unittest
import json
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "xy-cleanup"))
sys.path.insert(0, str(ROOT / "xy-repo-map" / "tests"))

import approvals  # noqa: E402
from map_fixture import build_map, feature, make_cleanup_repo, make_repo  # noqa: E402


COMMAND = ROOT / "xy-cleanup" / "approvals.py"


class ApprovalPlanTest(unittest.TestCase):
    def setUp(self):
        self.repository = make_cleanup_repo(self)

    def run_approvals(self, *arguments, timeout=None):
        return subprocess.run(
            ["python3", "-B", str(COMMAND), *arguments],
            capture_output=True, text=True, check=False, timeout=timeout,
        )

    def plan(self, scope=("全部",), review="变过的"):
        return self.run_approvals(
            "plan", "--repo", str(self.repository), "--scope", *scope,
            "--review", review,
        )

    def record(self, scope=("全部",), kept_markers=("无",)):
        return self.run_approvals(
            "record", "--repo", str(self.repository), "--features", *scope,
            "--kept-markers", *kept_markers,
        )

    def commit_changes(self, message):
        subprocess.run(["git", "-C", str(self.repository), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.repository), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.repository), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repository), "commit", "-m", message],
                       check=True, capture_output=True, text=True)
        return subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def set_item_status(self, feature_id, status):
        path = self.repository / "repo-map" / "features.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        pending = [document]
        while pending:
            feature_value = pending.pop()
            if feature_value["id"] == feature_id:
                feature_value["items"][0]["status"] = status
                break
            pending.extend(feature_value["children"])
        else:
            self.fail(f"找不到功能 {feature_id}")
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.repository), "add", "repo-map/features.json"],
            check=True,
        )

    def test_plan_does_not_fingerprint_files_outside_its_scope(self):
        data = build_map(self, self.repository)
        values, _ = approvals.feature_values(self.repository)
        beta_files = {
            path
            for group_paths in data["features"]["beta"]["files"].values()
            for path in group_paths
        }
        self.assertIn("beta/main.py", beta_files)
        read_files = []
        read_bytes = Path.read_bytes

        def record_read(path):
            read_files.append(path.relative_to(self.repository).as_posix())
            return read_bytes(path)

        output = io.StringIO()
        with mock.patch.object(approvals, "build_map", return_value=data), \
                mock.patch.object(approvals, "feature_values", return_value=(values, [])), \
                mock.patch.object(Path, "read_bytes", record_read), \
                redirect_stdout(output):
            result = approvals.main([
                "approvals.py", "plan", "--repo", str(self.repository),
                "--scope", "alpha", "--review", "变过的",
            ])

        self.assertEqual(result, 0, output.getvalue())
        self.assertNotIn("beta/main.py", read_files)

    def test_record_writes_sorted_fingerprints_for_the_scope(self):
        result = self.record()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.commit_changes("记录全部功能认可")
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
            ["approved.json", "features.json", "skip.json"],
        )
        plan = self.plan()
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertEqual(plan.stdout, "范围内没有变过的功能（范围内 3 个）\n")

    def test_recording_parent_does_not_include_children(self):
        result = self.record(("root",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = json.loads(
            (self.repository / "repo-map" / "approved.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(records), {"root"})

    def test_record_features_all_records_every_feature(self):
        result = self.record(("全部",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = json.loads(
            (self.repository / "repo-map" / "approved.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(records), {"root", "alpha", "beta"})

    def test_record_rejects_unknown_feature_ids_with_exit_two(self):
        result = self.record(("missing",))

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("missing", result.stdout + result.stderr)
        self.assertFalse((self.repository / "repo-map" / "approved.json").exists())

    def test_record_does_not_accept_legacy_scope_option(self):
        result = self.run_approvals(
            "record", "--repo", str(self.repository), "--scope", "alpha",
            "--kept-markers", "无",
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_record_requires_kept_markers(self):
        result = self.run_approvals(
            "record", "--repo", str(self.repository), "--features", "alpha",
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--kept-markers", result.stderr)

    def test_record_rejects_a_graph_marker_that_was_not_listed(self):
        path = self.repository / "unmapped.py"
        path.write_text("value = 3\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.repository), "add", "unmapped.py"], check=True,
        )

        result = self.record()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("功能图有但 --kept-markers 未列", result.stdout)
        self.assertIn("root:没归到功能的代码或测试", result.stdout)
        self.assertFalse((self.repository / "repo-map" / "approved.json").exists())

    def test_record_rejects_a_listed_marker_missing_from_the_graph(self):
        result = self.record(kept_markers=("alpha:还没实现",))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("--kept-markers 有但功能图没有", result.stdout)
        self.assertIn("alpha:还没实现", result.stdout)
        self.assertFalse((self.repository / "repo-map" / "approved.json").exists())

    def test_record_accepts_the_exact_markers_for_the_scope(self):
        path = self.repository / "unmapped.py"
        path.write_text("value = 3\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.repository), "add", "unmapped.py"], check=True,
        )

        result = self.record(kept_markers=("root:没归到功能的代码或测试",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_record_cannot_keep_the_pending_decision_marker(self):
        temporary_repo = make_repo(self, {
            "README.md": "Demo.\n",
            "alpha/main.py": "value = 1\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("alpha", "alpha", source="起草"),
        ]))

        result = self.run_approvals(
            "record", "--repo", str(temporary_repo), "--features", "alpha",
            "--kept-markers", "alpha:等你拍板",
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("仍是 起草", result.stdout)
        self.assertFalse((temporary_repo / "repo-map" / "approved.json").exists())

    def test_plan_rejects_approved_paths_that_are_not_regular_files(self):
        approved_path = self.repository / "repo-map" / "approved.json"
        cases = ("symlink", "directory")
        for kind in cases:
            with self.subTest(kind=kind):
                if kind == "symlink":
                    approved_path = self.repository / "repo-map" / "approved.json"
                    external_path = self.repository.parent / "outside-approved.json"
                    external_path.write_text("{}\n", encoding="utf-8")
                    approved_path.symlink_to(external_path)
                    original_content = external_path.read_text(encoding="utf-8")
                else:
                    approved_path.mkdir()
                    external_path = None
                result = self.plan()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("认可记录", result.stdout)
                self.assertEqual(result.stderr, "")
                self.assertTrue(approved_path.is_symlink() if kind == "symlink"
                                else approved_path.is_dir())
                if external_path is not None:
                    self.assertEqual(external_path.read_text(encoding="utf-8"), original_content)
                if kind == "directory":
                    approved_path.rmdir()
                else:
                    approved_path.unlink()

    def test_plan_rejects_approved_fifo_without_blocking(self):
        approved_path = self.repository / "repo-map" / "approved.json"
        os.mkfifo(approved_path)

        try:
            result = self.run_approvals(
                "plan", "--repo", str(self.repository), "--scope", "全部",
                "--review", "变过的", timeout=5,
            )
        except subprocess.TimeoutExpired:
            self.fail("plan 读取 FIFO 时 5 秒内未退出")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("认可记录必须是仓库 repo-map 目录下的普通文件", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_record_rejects_repo_map_directory_symlink_without_creating_files(self):
        repo_map_path = self.repository / "repo-map"
        external_directory = self.repository.parent / "outside-repo-map"
        external_directory.mkdir()
        for filename in ("features.json", "skip.json"):
            (external_directory / filename).write_bytes(
                (repo_map_path / filename).read_bytes()
            )
            (repo_map_path / filename).unlink()
        repo_map_path.rmdir()
        repo_map_path.symlink_to(external_directory, target_is_directory=True)

        result = self.record()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("认可记录", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            sorted(path.name for path in external_directory.iterdir()),
            ["features.json", "skip.json"],
        )

    def test_plan_expands_parent_scope_and_can_review_every_feature(self):
        changed = self.plan(("root",))
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        self.assertEqual(
            changed.stdout,
            "root\t没有认可记录\t无\nalpha\t没有认可记录\t无\n"
            "beta\t没有认可记录\t无\n要审 3 个功能（范围内 3 个）\n",
        )

        all_features = self.plan(("root",), "全部")
        self.assertEqual(all_features.returncode, 0, all_features.stdout + all_features.stderr)
        self.assertEqual(
            all_features.stdout,
            "root\t全部重审\t无\nalpha\t全部重审\t无\nbeta\t全部重审\t无\n"
            "要审 3 个功能（范围内 3 个）\n",
        )

    def test_plan_detects_only_a_changed_feature_file(self):
        recorded = self.record()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        approval_commit = self.commit_changes("记录全部功能认可")

        (self.repository / "alpha" / "main.py").write_text("value = 99\n", encoding="utf-8")
        result = self.plan()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            f"alpha\t认可后变过\t{approval_commit}\n要审 1 个功能（范围内 3 个）\n",
        )

    def test_plan_tracks_each_manifest_entry_without_crossing_to_siblings(self):
        recorded = self.record()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        approval_commit = self.commit_changes("记录全部功能认可")
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
            f"beta\t认可后变过\t{approval_commit}\n要审 1 个功能（范围内 1 个）\n",
        )

    def test_subrepository_registration_changes_only_root_fingerprint_and_plan(self):
        original_map = build_map(self, self.repository)
        original_values, _ = approvals.feature_values(self.repository)
        original_fingerprints = approvals.feature_fingerprints(
            self.repository, original_map, original_values
        )
        approved_path = self.repository / "repo-map" / "approved.json"
        approved_path.write_text(
            json.dumps(original_fingerprints, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        approval_commit = self.commit_changes("记录原始认可")

        manifest_path = self.repository / "repo-map" / "features.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["subrepositories"] = ["quant-deep"]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        changed_map = build_map(self, self.repository)
        changed_values, _ = approvals.feature_values(self.repository)
        changed_fingerprints = approvals.feature_fingerprints(
            self.repository, changed_map, changed_values
        )

        self.assertNotEqual(original_fingerprints["root"], changed_fingerprints["root"])
        self.assertEqual(original_fingerprints["alpha"], changed_fingerprints["alpha"])
        self.assertEqual(original_fingerprints["beta"], changed_fingerprints["beta"])

        result = self.plan()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            f"root\t认可后变过\t{approval_commit}\n要审 1 个功能（范围内 3 个）\n",
        )

    def test_plan_ignores_approved_file_formatting_changes(self):
        recorded = self.record()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.commit_changes("记录全部功能认可")
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
        approval_commit = self.commit_changes("记录全部功能认可")

        link_path.unlink()
        link_path.symlink_to("second-target")
        result = self.plan(("alpha",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            f"alpha\t认可后变过\t{approval_commit}\n要审 1 个功能（范围内 1 个）\n",
        )

    def test_plan_reports_latest_commit_that_recorded_a_feature_twice(self):
        self.commit_changes("初始提交")
        first_record = self.record(("alpha",))
        self.assertEqual(first_record.returncode, 0, first_record.stdout + first_record.stderr)
        self.commit_changes("首次认可 alpha")

        (self.repository / "alpha" / "main.py").write_text("value = 10\n", encoding="utf-8")
        second_record = self.record(("alpha",))
        self.assertEqual(second_record.returncode, 0, second_record.stdout + second_record.stderr)
        second_commit = self.commit_changes("再次认可 alpha")
        (self.repository / "alpha" / "main.py").write_text("value = 11\n", encoding="utf-8")

        result = self.plan(("alpha",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            f"alpha\t认可后变过\t{second_commit}\n要审 1 个功能（范围内 1 个）\n",
        )

    def test_plan_uses_the_commit_that_added_this_feature_record(self):
        self.commit_changes("初始提交")
        alpha_record = self.record(("alpha",))
        self.assertEqual(alpha_record.returncode, 0, alpha_record.stdout + alpha_record.stderr)
        alpha_commit = self.commit_changes("认可 alpha")

        beta_record = self.record(("beta",))
        self.assertEqual(beta_record.returncode, 0, beta_record.stdout + beta_record.stderr)
        self.commit_changes("认可 beta")
        (self.repository / "alpha" / "main.py").write_text("value = 99\n", encoding="utf-8")

        result = self.plan(("alpha",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            f"alpha\t认可后变过\t{alpha_commit}\n要审 1 个功能（范围内 1 个）\n",
        )

    def test_plan_rejects_an_uncommitted_approval_record(self):
        self.commit_changes("初始提交")
        recorded = self.record(("alpha",))
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

        result = self.plan(("alpha",))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("先提交 repo-map/approved.json", result.stdout + result.stderr)

    def test_record_rejects_drafts_without_writing(self):
        temporary_repo = make_repo(self, {
            "README.md": "Demo.\n",
            "alpha/main.py": "value = 1\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("alpha", "alpha", source="起草"),
        ]))
        result = self.run_approvals(
            "record", "--repo", str(temporary_repo), "--features", "alpha",
            "--kept-markers", "无",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertFalse((temporary_repo / "repo-map" / "approved.json").exists())

    def test_record_rejects_an_in_scope_draft_item_without_writing(self):
        self.set_item_status("alpha", "起草")

        result = self.record(("alpha",))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("alpha", result.stdout)
        self.assertIn("items.status", result.stdout)
        self.assertFalse((self.repository / "repo-map" / "approved.json").exists())

    def test_record_allows_a_draft_item_outside_the_selected_scope(self):
        self.set_item_status("alpha", "起草")

        result = self.record(("beta",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = json.loads(
            (self.repository / "repo-map" / "approved.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(records), {"beta"})

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
                result = self.plan(("alpha",))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("认可记录", result.stdout)
                self.assertEqual(result.stderr, "")

    def test_scope_arguments_reject_mixed_all_duplicates_and_unknown_ids(self):
        for arguments in (
            ("plan", "--repo", str(self.repository), "--scope", "全部", "alpha",
             "--review", "变过的"),
            ("record", "--repo", str(self.repository), "--features", "alpha", "alpha",
             "--kept-markers", "无"),
            ("plan", "--repo", str(self.repository), "--scope", "missing",
             "--review", "变过的"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_approvals(*arguments)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
