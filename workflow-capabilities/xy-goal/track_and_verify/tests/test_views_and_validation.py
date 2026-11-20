from track_test_support import GoalTestSupport, StringIO, goal, redirect_stdout
from track_and_verify.operations import show


class ViewsAndValidationTests(GoalTestSupport):

    def test_contract_shares_item_with_runner_and_agent_files(self):
        contract = self.open_goal("release")
        runner = contract.parent / "sub-agent-runner" / "tasks"
        runner.mkdir(parents=True)
        (runner / "job.json").write_bytes(b'{"state": "done"}\n')
        (contract.parent / "report.md").write_text("agent 报告")
        ordinary = self.root / "ordinary" / "item" / "xy-goal"
        ordinary.mkdir(parents=True)
        (ordinary / "goal.md").write_text("普通目录里的非契约")
        self.hook("hook-prompt", {"session_id": self.session, "prompt": "并存的任务"})
        self.add_task(1)
        self.succeeds("show")
        self.succeeds("check")
        self.succeeds("update", "T1", "--pointer", str(contract.parent / "report.md"))
        self.succeeds("accept", "T1", "--verdict", "通过")
        before = self.files()
        self.close_contract(contract)
        del before[str((contract / "goal.md").relative_to(self.root))]
        self.assertEqual(self.files(), before)


    def test_show_lists_goal_and_unhandled_quotes_in_numeric_order(self):
        contract = self.open_goal("release")
        for number in (10, 2, 1):
            self.quote(contract, number, "不要漏掉要求")
        output = self.succeeds("show")
        feature_output = StringIO()
        with redirect_stdout(feature_output):
            show(goal.Contract(contract, (contract / "goal.md").read_bytes()))
        self.assertEqual(feature_output.getvalue(), output)
        self.assertIn("交付新版本", output)
        self.assertIn("还没处理的原话：1、2、10", output)


    def test_check_reports_each_unhandled_quote_then_passes_without_them(self):
        contract = self.open_goal("release")
        self.assertIn("通过", self.succeeds("check"))
        self.quote(contract, 1, "实现功能")
        self.quote(contract, 2, "保留接口")
        errors = self.rejects("check", message="原话 1")
        self.assertIn("原话 2", errors)
        self.assertEqual(len(errors.splitlines()), 2)


    def test_all_commands_refuse_corrupt_contract_files_and_name_the_file(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布")
        self.add_task(1)
        for relative, corrupt in (("goal.md", "target: 12\nsession_id: \"session-main\"\nclaude_pid: \"1\"\n"),
                                  ("tasks/T1.md", "字段坏了"),
                                  ("links.md", "1 T99\n"),
                                  ("links.md", "9 T1\n"),
                                  ("links.md", "坏关系\n"),
                                  ("done/T2.md", "损坏")):
            with self.subTest(relative=relative, corrupt=corrupt):
                path = contract / relative
                original = path.read_bytes() if path.exists() else None
                path.write_text(corrupt, encoding="utf-8")
                self.rejects("show", message=str(path))
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)


    def test_show_warns_at_end_without_rejecting_long_writes(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "完成需求")
        self.succeeds("add", "--title", "长任务", "--criteria", "判" * 5100,
                      "--source-kind", "原话直提", "--source", "1",
                      "--state", "在办", "--pointer", "无")
        output = self.succeeds("show")
        self.assertIn("T1", output.splitlines()[-2])
        self.assertIn("300", output.splitlines()[-2])
        self.assertIn("5000", output.splitlines()[-1])
        self.succeeds("check")
