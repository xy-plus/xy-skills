from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest


ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
REPOSITORY_CARE = ROOT / "repository-care"
XY_CLEANUP_DIRECTORY = REPOSITORY_CARE / "xy-cleanup"
sys.path.insert(0, str(REPOSITORY_CARE / "tests"))
sys.path.insert(0, str(XY_CLEANUP_DIRECTORY / "changed_features_only"))

from map_fixture import make_cleanup_repo  # noqa: E402


COMMAND = XY_CLEANUP_DIRECTORY / "changed_features_only" / "approvals.py"
FEATURE_IDS = ("root", "alpha", "beta")


def run_git(repository, *arguments):
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def decisions_document(title, feature_ids):
    return {
        "title": title,
        "intro": ["每个功能只选一项。"],
        "groups": [
            {
                "title": "功能审批",
                "notes": [],
                "items": [
                    {
                        "id": feature_id,
                        "title": feature_id,
                        "lines": ["认可后变过。"],
                        "options": ["照建议", "留", "留，下次还问我"],
                        "recommended": "照建议",
                        "compare": None,
                        "details": None,
                    }
                    for feature_id in feature_ids
                ],
            }
        ],
        "footnotes": [],
    }


def answers_document(title, feature_ids, choices=None):
    choices = choices or {}
    return {
        "title": title,
        "answers": {
            feature_id: {
                "option": choices.get(feature_id, "照建议"),
                "note": "",
            }
            for feature_id in feature_ids
        },
    }


class ApprovalPlanTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.primary = make_cleanup_repo(self)
        run_git(self.primary, "config", "user.name", "Test")
        run_git(self.primary, "config", "user.email", "test@example.com")
        run_git(self.primary, "add", "-A")
        run_git(self.primary, "commit", "-m", "建立测试仓库")
        self.approval_base = run_git(self.primary, "rev-parse", "HEAD")

        approved_path = self.primary / "repo-map" / "approved.json"
        approved_path.write_text(
            json.dumps(
                {feature_id: self.approval_base for feature_id in FEATURE_IDS}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        run_git(self.primary, "add", "repo-map/approved.json")
        run_git(self.primary, "commit", "-m", "记录测试基线")
        self.plan_commit = run_git(self.primary, "rev-parse", "HEAD")

        self.repository = self.parent / "linked-repository"
        run_git(
            self.primary,
            "worktree",
            "add",
            "--detach",
            str(self.repository),
            self.plan_commit,
        )

    def run_approvals(self, *arguments):
        return subprocess.run(
            ["python3", "-B", str(COMMAND), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    def plan(self, repository=None, scope=("全部",), review="变过的"):
        return self.run_approvals(
            "plan",
            "--repo",
            str(repository or self.repository),
            "--scope",
            *scope,
            "--review",
            review,
        )

    def commit_changes(self, message, repository=None):
        target = repository or self.repository
        run_git(target, "add", "-A")
        run_git(target, "commit", "-m", message)
        return run_git(target, "rev-parse", "HEAD")

    def title(self, commit=None):
        return f"清扫决定：{self.primary.name} 计划提交 {commit or self.plan_commit}"

    def record_files(self, feature_ids=("alpha",), title=None, choices=None):
        selected_title = title or self.title()
        decisions_path = self.parent / "decisions.json"
        answers_path = self.parent / "answers.json"
        decisions_path.write_text(
            json.dumps(
                decisions_document(selected_title, feature_ids), ensure_ascii=False
            ),
            encoding="utf-8",
        )
        answers_path.write_text(
            json.dumps(
                answers_document(selected_title, feature_ids, choices),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return decisions_path, answers_path

    def record(
        self,
        feature_ids=("alpha",),
        repository=None,
        title=None,
        choices=None,
        mainline=None,
    ):
        decisions_path, answers_path = self.record_files(
            feature_ids, title, choices
        )
        return self.run_record_files(
            decisions_path, answers_path, repository, mainline
        )

    def run_record_files(
        self, decisions_path, answers_path, repository=None, mainline=None
    ):
        return self.run_approvals(
            "record",
            "--repo",
            str(repository or self.repository),
            "--decisions",
            str(decisions_path),
            "--answers",
            str(answers_path),
            "--mainline",
            mainline or self.plan_commit,
        )

    def test_plan_uses_committed_approvals_and_clean_worktree(self):
        result = self.plan()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "范围内没有变过的功能（范围内 3 个）\n")

        (self.repository / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        untracked = self.plan()
        self.assertEqual(untracked.returncode, 0, untracked.stdout + untracked.stderr)
        self.assertEqual(untracked.stdout, "范围内没有变过的功能（范围内 3 个）\n")

        (self.repository / "alpha" / "main.py").write_text(
            "value = 10\n", encoding="utf-8"
        )
        tracked = self.plan()
        self.assertEqual(tracked.returncode, 1, tracked.stdout + tracked.stderr)
        self.assertIn("已跟踪文件有改动", tracked.stdout + tracked.stderr)
        self.assertNotIn("工作树必须干净", tracked.stdout + tracked.stderr)

    def test_plan_detects_only_changed_owned_file(self):
        (self.repository / "alpha" / "main.py").write_text(
            "value = 10\n", encoding="utf-8"
        )
        self.commit_changes("改 alpha")

        result = self.plan(scope=("alpha",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            f"alpha\t认可后改过\t{self.approval_base}\n要审 1 个功能（范围内 1 个）\n",
        )

    def test_pure_rename_inside_same_feature_does_not_trigger_review(self):
        run_git(self.repository, "mv", "alpha/main.py", "alpha/renamed.py")
        self.commit_changes("改 alpha 内部文件名")

        result = self.plan(scope=("alpha",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "范围内没有变过的功能（范围内 1 个）\n")

    def test_changed_rename_inside_same_feature_triggers_review(self):
        (self.repository / "alpha" / "main.py").write_text(
            "value = 100\n", encoding="utf-8"
        )
        run_git(self.repository, "add", "alpha/main.py")
        run_git(self.repository, "mv", "alpha/main.py", "alpha/renamed.py")
        self.commit_changes("改名并修改 alpha")

        result = self.plan(scope=("alpha",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("alpha\t认可后改过\t", result.stdout)

    def test_scope_expands_parents_and_full_review_includes_unchanged_features(self):
        parent = self.plan(scope=("root",))
        all_features = self.plan(scope=("alpha",), review="全部")

        self.assertEqual(parent.returncode, 0, parent.stdout + parent.stderr)
        self.assertEqual(parent.stdout, "范围内没有变过的功能（范围内 3 个）\n")
        self.assertEqual(
            all_features.returncode, 0, all_features.stdout + all_features.stderr
        )
        self.assertEqual(
            all_features.stdout,
            "alpha\t全部重审\t"
            + self.approval_base
            + "\n要审 1 个功能（范围内 1 个）\n",
        )

    def test_changed_review_includes_unchanged_features_in_an_existing_cycle_in_scope_order(
        self,
    ):
        features_path = self.primary / "repo-map" / "features.json"
        features = json.loads(features_path.read_text(encoding="utf-8"))
        features["existing_cycle_imports"] = [
            {"from": "alpha", "to": "beta"},
            {"from": "beta", "to": "alpha"},
        ]
        features_path.write_text(
            json.dumps(features, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (self.primary / "alpha" / "main.py").write_text(
            "import beta.main\n", encoding="utf-8"
        )
        (self.primary / "beta" / "main.py").write_text(
            "import alpha.main\n", encoding="utf-8"
        )
        cycle_commit = self.commit_changes("登记测试循环依赖", self.primary)
        approved_path = self.primary / "repo-map" / "approved.json"
        approved_path.write_text(
            json.dumps(
                {feature_id: cycle_commit for feature_id in FEATURE_IDS}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        plan_commit = self.commit_changes("认可循环依赖测试基线", self.primary)
        cycle_worktree = self.parent / "cycle-worktree"
        run_git(
            self.primary,
            "worktree",
            "add",
            "--detach",
            str(cycle_worktree),
            plan_commit,
        )

        result = self.plan(repository=cycle_worktree)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            f"alpha\t有存量互相引用\t{cycle_commit}\n"
            f"beta\t有存量互相引用\t{cycle_commit}\n"
            "要审 2 个功能（范围内 3 个）\n",
        )

    def test_feature_registration_changes_are_scoped(self):
        path = self.repository / "repo-map" / "features.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["children"][0]["title"] = "Alpha updated"
        path.write_text(
            json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self.commit_changes("改 alpha 登记")

        result = self.plan(scope=("alpha",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("alpha\t认可后改过\t", result.stdout)

    def test_directory_field_and_pure_feature_directory_rename_do_not_trigger_review(
        self,
    ):
        run_git(self.repository, "mv", "alpha", "renamed-alpha")
        path = self.repository / "repo-map" / "features.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["children"][0]["directory"] = "renamed-alpha"
        path.write_text(
            json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self.commit_changes("移动 alpha 功能目录")

        result = self.plan(scope=("alpha",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "范围内没有变过的功能（范围内 1 个）\n")

    def test_skip_set_change_marks_root_and_blocks_scope_without_root(self):
        skip_path = self.repository / "repo-map" / "skip.json"
        skip_path.write_text('["README.md"]\n', encoding="utf-8")
        self.commit_changes("改跳过边界")

        scoped = self.plan(scope=("alpha",))
        self.assertEqual(scoped.returncode, 1, scoped.stdout + scoped.stderr)
        self.assertIn("跳过名单改过", scoped.stdout + scoped.stderr)

        whole = self.plan()
        self.assertEqual(whole.returncode, 0, whole.stdout + whole.stderr)
        self.assertIn("root\t认可后改过\t", whole.stdout)

    def test_skip_format_only_change_does_not_mark_root(self):
        skip_path = self.repository / "repo-map" / "skip.json"
        skip_path.write_text("[ ]\n", encoding="utf-8")
        self.commit_changes("只改跳过名单格式")

        result = self.plan(scope=("root",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "范围内没有变过的功能（范围内 3 个）\n")

    def test_scope_refuses_registration_changes_outside_scope(self):
        path = self.repository / "repo-map" / "features.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["children"][1]["what"] = "改变 beta 的结果。"
        path.write_text(
            json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self.commit_changes("改范围外 beta 登记")

        result = self.plan(scope=("alpha",))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("beta", result.stdout + result.stderr)
        self.assertIn("把范围改成「全部」", result.stdout + result.stderr)

    def test_scope_refuses_file_ownership_move_across_scope_boundary(self):
        (self.repository / "alpha" / "keep.py").write_text(
            "value = 3\n", encoding="utf-8"
        )
        self.commit_changes("补 alpha 跟踪文件")
        self.plan_commit = run_git(self.repository, "rev-parse", "HEAD")
        run_git(self.repository, "mv", "alpha/main.py", "beta/from-alpha.py")
        self.commit_changes("把文件从 alpha 移到 beta")

        result = self.plan(scope=("alpha",))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("alpha/main.py", result.stdout + result.stderr)
        self.assertIn("beta/from-alpha.py", result.stdout + result.stderr)

    def test_scope_rejects_old_feature_map_without_directory(self):
        legacy = make_cleanup_repo(self)
        run_git(legacy, "config", "user.name", "Test")
        run_git(legacy, "config", "user.email", "test@example.com")
        run_git(legacy, "add", "-A")
        run_git(legacy, "commit", "-m", "建立旧格式测试仓")
        manifest = legacy / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))

        def remove_directories(feature_value):
            feature_value.pop("directory", None)
            for child in feature_value["children"]:
                remove_directories(child)

        remove_directories(document)
        manifest.write_text(json.dumps(document), encoding="utf-8")
        run_git(legacy, "add", "repo-map/features.json")
        run_git(legacy, "commit", "-m", "旧格式功能图")
        old_head = run_git(legacy, "rev-parse", "HEAD")
        approved = legacy / "repo-map" / "approved.json"
        approved.write_text(
            json.dumps({feature_id: old_head for feature_id in FEATURE_IDS}),
            encoding="utf-8",
        )
        run_git(legacy, "add", "repo-map/approved.json")
        run_git(legacy, "commit", "-m", "旧格式认可记录")

        current = json.loads(manifest.read_text(encoding="utf-8"))

        def add_directories(feature_value, parent=""):
            feature_id = feature_value["id"]
            feature_value["directory"] = "" if feature_id == "root" else feature_id
            for child in feature_value["children"]:
                add_directories(child, feature_id)

        add_directories(current)
        manifest.write_text(json.dumps(current), encoding="utf-8")
        run_git(legacy, "add", "repo-map/features.json")
        run_git(legacy, "commit", "-m", "升级功能图格式")
        worktree = self.parent / "old-format-worktree"
        run_git(legacy, "worktree", "add", "--detach", str(worktree), "HEAD")

        result = self.plan(repository=worktree, scope=("alpha",))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("分不清改动归谁", result.stdout + result.stderr)
        self.assertIn("--review 全部", result.stdout + result.stderr)

    def test_plan_rejects_invalid_or_non_ancestor_approval_commits(self):
        approved_path = self.repository / "repo-map" / "approved.json"
        records = json.loads(approved_path.read_text(encoding="utf-8"))
        records["alpha"] = "a" * 40
        approved_path.write_text(json.dumps(records), encoding="utf-8")
        self.commit_changes("写入不存在的认可提交")

        result = self.plan(scope=("alpha",))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("不存在", result.stdout + result.stderr)

    def test_plan_rejects_null_approval_commit_with_readable_error(self):
        approved_path = self.repository / "repo-map" / "approved.json"
        records = json.loads(approved_path.read_text(encoding="utf-8"))
        records["alpha"] = None
        approved_path.write_text(json.dumps(records), encoding="utf-8")
        self.commit_changes("写入 null 认可记录")

        result = self.plan(scope=("alpha",))

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("认可记录 alpha 的提交号必须是字符串", output)
        self.assertNotIn("Traceback", output)

    def test_plan_rejects_array_approval_commit_with_readable_error(self):
        approved_path = self.repository / "repo-map" / "approved.json"
        records = json.loads(approved_path.read_text(encoding="utf-8"))
        records["alpha"] = []
        approved_path.write_text(json.dumps(records), encoding="utf-8")
        self.commit_changes("写入数组认可记录")

        result = self.plan(scope=("alpha",))

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("认可记录 alpha 的提交号必须是字符串", output)
        self.assertNotIn("Traceback", output)

    def test_record_updates_only_answered_features_and_drops_stale_ids(self):
        approved_path = self.repository / "repo-map" / "approved.json"
        records = json.loads(approved_path.read_text(encoding="utf-8"))
        records["stale-feature"] = self.approval_base
        approved_path.write_text(json.dumps(records) + "\n", encoding="utf-8")
        self.commit_changes("添加待清理的旧功能记录")
        self.plan_commit = run_git(self.repository, "rev-parse", "HEAD")

        result = self.record(("alpha",))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(approved_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["alpha"], self.plan_commit)
        self.assertEqual(saved["root"], self.approval_base)
        self.assertEqual(saved["beta"], self.approval_base)
        self.assertNotIn("stale-feature", saved)

    def test_record_rejects_answer_title_mismatch(self):
        decisions_path, answers_path = self.record_files(("alpha",))
        answers = json.loads(answers_path.read_text(encoding="utf-8"))
        answers["title"] = self.title(self.approval_base)
        answers_path.write_text(json.dumps(answers), encoding="utf-8")

        result = self.run_record_files(decisions_path, answers_path)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("title", result.stdout + result.stderr)
        self.assertIn("一致", result.stdout + result.stderr)

    def test_record_rejects_incomplete_or_invalid_answers(self):
        decisions_path, answers_path = self.record_files(("alpha",))
        answers_path.write_text(
            json.dumps({"title": self.title(), "answers": {}}), encoding="utf-8"
        )

        result = self.run_record_files(decisions_path, answers_path)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("缺少回答", result.stdout + result.stderr)

    def test_record_rejects_reusing_a_decision_after_it_was_recorded(self):
        first = self.record(("alpha",))
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.commit_changes("记录 alpha 已审")

        second = self.record(("alpha",))

        self.assertEqual(second.returncode, 1, second.stdout + second.stderr)
        self.assertIn("已经记过", second.stdout + second.stderr)

    def test_reask_answer_is_rejected_when_the_decision_was_already_recorded(self):
        first = self.record(("alpha",))
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.commit_changes("记录首次回答")

        second = self.record(("alpha",), choices={"alpha": "留，下次还问我"})

        self.assertEqual(second.returncode, 1, second.stdout + second.stderr)
        self.assertIn("已经记过", second.stdout + second.stderr)

    def test_record_requires_clean_linked_worktree(self):
        (self.repository / "alpha" / "main.py").write_text("dirty\n", encoding="utf-8")

        result = self.record(("alpha",))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("工作树必须干净", result.stdout + result.stderr)

    def test_record_requires_an_explicit_mainline(self):
        decisions_path, answers_path = self.record_files(("alpha",))
        result = self.run_approvals(
            "record",
            "--repo",
            str(self.repository),
            "--decisions",
            str(decisions_path),
            "--answers",
            str(answers_path),
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--mainline", result.stderr)

    def test_record_rejects_primary_checkout(self):
        primary_title = f"清扫决定：{self.primary.name} 计划提交 {self.plan_commit}"
        result = self.record(("alpha",), repository=self.primary, title=primary_title)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("主检出", result.stdout + result.stderr)

    def test_record_rejects_linked_worktree_name_in_decision_title(self):
        result = self.record(
            ("alpha",),
            title=f"清扫决定：{self.repository.name} 计划提交 {self.plan_commit}",
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("title", result.stdout + result.stderr)

    def test_record_requires_the_plan_commit_on_the_first_parent_chain(self):
        run_git(self.repository, "reset", "--hard", self.approval_base)
        run_git(self.repository, "switch", "-c", "alternate-history")
        (self.repository / "alpha" / "main.py").write_text(
            "value = alternate\n", encoding="utf-8"
        )
        self.commit_changes("创建另一条历史")
        run_git(
            self.repository,
            "merge",
            "--no-ff",
            "--no-edit",
            self.plan_commit,
        )

        result = self.record(("alpha",), mainline=self.plan_commit)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("第一父链", result.stdout + result.stderr)

    def test_reask_removes_old_approval_and_allows_draft_items(self):
        path = self.repository / "repo-map" / "features.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["children"][0]["items"][0]["status"] = "起草"
        path.write_text(json.dumps(document), encoding="utf-8")
        self.commit_changes("起草 alpha")

        result = self.record(
            ("alpha",), choices={"alpha": "留，下次还问我"}
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(
            (self.repository / "repo-map" / "approved.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("alpha", saved)

    def test_record_keeps_features_changed_by_a_merged_mainline(self):
        for index, change in enumerate(
            ("beta-file", "beta-registration-draft", "beta-rename")
        ):
            with self.subTest(change=change):
                if index:
                    self._reset_mainline_merge_fixture()
                self._merge_mainline_change(change)

                result = self.record(("alpha", "beta"), mainline="mainline")

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                saved = json.loads(
                    (self.repository / "repo-map" / "approved.json").read_text(
                        encoding="utf-8"
                    )
                )
                head = run_git(self.repository, "rev-parse", "HEAD")
                self.assertEqual(saved["alpha"], head)
                self.assertEqual(saved["beta"], self.approval_base)
                self.assertIn("留到下次清扫：beta（主线带来改动）", result.stdout)

    def test_record_keeps_features_changed_by_a_fast_forward_mainline(self):
        run_git(self.primary, "switch", "-c", "fast-forward-mainline")
        (self.primary / "beta" / "main.py").write_text(
            "value = 20\n", encoding="utf-8"
        )
        run_git(self.primary, "add", "-A")
        run_git(self.primary, "commit", "-m", "主线改动 beta")
        mainline_head = run_git(self.primary, "rev-parse", "HEAD")
        run_git(self.repository, "merge", "--ff-only", "fast-forward-mainline")

        result = self.record(("alpha", "beta"), mainline="fast-forward-mainline")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(
            (self.repository / "repo-map" / "approved.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(saved["alpha"], mainline_head)
        self.assertEqual(saved["beta"], self.approval_base)
        self.assertIn("留到下次清扫：beta（主线带来改动）", result.stdout)

    def test_record_records_answers_when_mainline_has_no_changes(self):
        result = self.record(("beta",), mainline=self.plan_commit)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(
            (self.repository / "repo-map" / "approved.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(saved["beta"], self.plan_commit)
        self.assertNotIn("留到下次清扫", result.stdout)

    def test_reask_removes_approval_even_when_mainline_changed_the_feature(self):
        self._merge_mainline_change("beta-file")

        result = self.record(
            ("beta",), choices={"beta": "留，下次还问我"}
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(
            (self.repository / "repo-map" / "approved.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("beta", saved)
        self.assertNotIn("留到下次清扫：beta（主线带来改动）", result.stdout)

    def _merge_mainline_change(self, change):
        run_git(self.repository, "switch", "-c", "cleanup-branch")
        (self.repository / "alpha" / "main.py").write_text(
            "value = 10\n", encoding="utf-8"
        )
        self.commit_changes("清扫分支自己的 alpha 改动")

        run_git(self.primary, "switch", "-c", "mainline")
        if change == "beta-file":
            (self.primary / "beta" / "main.py").write_text(
                "value = 20\n", encoding="utf-8"
            )
        elif change == "beta-registration-draft":
            path = self.primary / "repo-map" / "features.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["children"][1]["items"][0]["text"] = "Beta 主线改过的要求。"
            document["children"][1]["items"][0]["status"] = "起草"
            path.write_text(json.dumps(document), encoding="utf-8")
        elif change == "beta-rename":
            (self.primary / "beta" / "keep.py").write_text(
                "value = 2\n", encoding="utf-8"
            )
            run_git(self.primary, "mv", "beta/main.py", "beta-main.py")
        else:
            self.fail(f"未知主线改动：{change}")
        run_git(self.primary, "add", "-A")
        run_git(self.primary, "commit", "-m", "主线改动 beta")
        mainline_head = run_git(self.primary, "rev-parse", "HEAD")
        run_git(
            self.repository,
            "merge",
            "--no-ff",
            "--no-edit",
            mainline_head,
        )

    def _reset_mainline_merge_fixture(self):
        run_git(self.repository, "reset", "--hard", self.plan_commit)
        run_git(self.repository, "switch", "--detach", self.plan_commit)
        run_git(self.repository, "branch", "-D", "cleanup-branch")
        run_git(self.primary, "switch", "--detach", self.plan_commit)
        run_git(self.primary, "branch", "-D", "mainline")

    def test_record_rejects_drafts_and_old_features_argument(self):
        path = self.repository / "repo-map" / "features.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["children"][0]["items"][0]["status"] = "起草"
        path.write_text(json.dumps(document), encoding="utf-8")
        self.commit_changes("起草 alpha")

        draft = self.record(("alpha",), title=self.title())
        old_option = self.run_approvals(
            "record",
            "--repo",
            str(self.repository),
            "--features",
            "alpha",
        )

        self.assertEqual(draft.returncode, 1, draft.stdout + draft.stderr)
        self.assertIn("仍是 起草", draft.stdout + draft.stderr)
        self.assertEqual(
            old_option.returncode, 2, old_option.stdout + old_option.stderr
        )

    def test_scope_arguments_reject_duplicates_unknown_and_mixed_all(self):
        cases = (
            (("alpha", "alpha"), "重复"),
            (("missing",), "未知"),
            (("全部", "alpha"), "全部"),
        )
        for scope, message in cases:
            with self.subTest(scope=scope):
                result = self.plan(scope=scope)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(message, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
