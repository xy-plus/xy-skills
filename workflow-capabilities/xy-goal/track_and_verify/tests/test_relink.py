import json
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from goal_test_support import (
    GoalTestSupport,
    StringIO,
    goal,
    mock,
    os,
    redirect_stdout,
)
from track_and_verify.operations import read_session_transcript, show


class RelinkTests(GoalTestSupport):

    def test_add_and_relink_source_supersede_dismissal(self):
        original_root = self.root
        for command in ("add", "relink"):
            with self.subTest(command=command):
                self.root = original_root / command
                self.root.mkdir()
                contract = self.open_goal("release")
                self.quote(contract, 1, "发布要求")
                if command == "add":
                    self.succeeds("dismiss", "1", "--reason", "已误标")
                    self.add_task(1)
                else:
                    self.add_task(1)
                    (contract / "links.md").write_text("1 不必跟进 已误标\n", encoding="utf-8")
                    with (contract / "links.md").open("a", encoding="utf-8") as links:
                        links.write("坏映射\n")
                    self.succeeds("relink", "T1", "--source", "1")
                self.assertEqual((contract / "links.md").read_text(encoding="utf-8"), "1 T1\n")
                self.succeeds("check")


    def test_relink_drops_machine_mapping_and_records_automatic_reason(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.add_task(1)
        self.quote(contract, 2, f"本次契约：{contract}。按 xy-goal 的「定时自查」一节做")
        (contract / "links.md").write_text("1 T1\n2 T1\n", encoding="utf-8")

        output = self.succeeds("relink")

        self.assertIn("丢弃：2 T1；原话 2 是机器输入（自动：本契约定时提示）", output)
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"),
                         "1 T1\n2 不必跟进 自动：本契约定时提示\n")
        self.succeeds("check")


    def test_relink_keeps_closed_machine_cancel_quote_without_error(self):
        contract = self.open_goal("release")
        done = self.add_closed_machine_cancel_quote(contract)
        before_links = (contract / "links.md").read_bytes()
        before_done = done.read_bytes()

        output = self.succeeds("relink")

        self.assertIn(f"已关闭事项的历史出处保留：{contract}", output)
        self.assertEqual((contract / "links.md").read_bytes(), before_links)
        self.assertEqual(done.read_bytes(), before_done)


    def test_relink_clears_open_machine_mapping_but_keeps_closed_cancel_history(self):
        contract = self.open_goal("release")
        done = self.add_closed_machine_cancel_quote(contract)
        self.add_task(1)
        self.quote(contract, 3, f"本次契约：{contract}。按 xy-goal 的「定时自查」一节做")
        (contract / "links.md").write_text("1 T1\n1 T2\n2 T1\n3 T2\n", encoding="utf-8")
        self.rejects("check", message="原话 3 是机器输入（自动：本契约定时提示）")

        output = self.succeeds("relink")

        self.assertIn("丢弃：3 T2；原话 3 是机器输入", output)
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"),
                         "1 T1\n1 T2\n2 T1\n3 不必跟进 自动：本契约定时提示\n")
        self.assertEqual(goal.Contract(contract, (contract / "goal.md").read_bytes()).done["T1"]["cancel_quote"], "2")
        self.assertTrue(done.exists())
        self.succeeds("check")


    def test_existing_dismissal_for_linked_quote_is_rejected_and_relink_cleans_it(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.add_task(1)
        (contract / "links.md").write_text("1 T1\n1 不必跟进 旧并存行\n", encoding="utf-8")

        self.rejects("check", message="原话 1 已挂事项，不能同时不必跟进")
        output = self.succeeds("relink")

        self.assertIn("丢弃：1 不必跟进 旧并存行", output)
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"), "1 T1\n")
        self.succeeds("check")


    def test_relink_commits_each_repair_before_remaining_contract_validation(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "第一项")
        self.quote(contract, 2, "第二项")
        self.add_task(1)
        self.add_task(1)
        self.add_task(2)
        (contract / "links.md").write_text("1 T1\n9 T2\n10 T3\n")
        before = self.files()
        code, output, errors = self.command("relink", "T2", "--source", "1")
        self.assertEqual(code, 1, output + errors)
        self.assertIn("9 T2", output)
        self.assertIn("10 T3", output)
        self.assertIn(f"{contract / 'tasks' / 'T3.md'}: 开放事项 T3 没有原话出处", errors)
        self.assertNotIn("修复完成", output)
        before[str((contract / "links.md").relative_to(self.root))] = b"1 T1\n1 T2\n"
        self.assertEqual(self.files(), before, "返回 1 仍保留本次修复，才能逐项收敛")
        output = self.succeeds("relink", "T3", "--source", "2")
        self.assertTrue(output.endswith(f"修复完成：{contract}\n"))
        self.assertEqual((contract / "links.md").read_text(), "1 T1\n1 T2\n2 T3\n")
        self.succeeds("check")


    def test_relink_keeps_valid_links_and_dismissals_and_prints_every_bad_line(self):
        contract = self.open_goal("release")
        for number in (1, 2, 3):
            self.quote(contract, number, "已有原话")
        self.add_task(1)
        self.add_task(3)
        self.succeeds("accept", "T2", "--verdict", "已验收")
        self.succeeds("dismiss", "2", "--reason", "已答")
        valid = "1 T1\n2 不必跟进 已答\n3 T2\n"
        bad = (("格式错误", "关系格式错误"),
               ("9 T1", "原话 9 不存在"),
               ("1 T99", "事项 T99 不存在"),
               ("1 T1", "关系重复"),
               ("2 不必跟进 重复", "不必跟进原因为空或重复"),
               ("3 不必跟进    ", "不必跟进原因为空或重复"))
        (contract / "links.md").write_text(valid + "".join(line + "\n" for line, _ in bad))
        before = self.files()
        output = self.succeeds("relink")
        for row, (line, reason) in zip(output.splitlines(), bad):
            self.assertIn(line, row)
            self.assertIn(reason, row)
        self.assertEqual(len(output.splitlines()), len(bad) + 1)
        before[str((contract / "links.md").relative_to(self.root))] = valid.encode("utf-8")
        self.assertEqual(self.files(), before)
        self.succeeds("check")


    def test_relink_refuses_healthy_contract_without_writes(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "正常要求")
        self.add_task(1)
        self.rejects("relink", message="契约完好，挂出处用 update")
        self.rejects("relink", "T1", "--source", "1", message="契约完好，挂出处用 update")


    def test_relink_requires_task_and_source_together_before_writing(self):
        contract = self.open_goal("release")
        (contract / "links.md").write_text("坏映射\n")
        for arguments in (("T1",), ("--source", "1")):
            with self.subTest(arguments=arguments):
                self.rejects("relink", *arguments, message="同时")
        self.rejects_argument_error(
            "relink", "T1", "--source", "不是数字", message="invalid int value",
        )


    def test_relink_rejects_closed_or_missing_tasks_and_missing_quotes_without_writes(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "原要求")
        self.add_task(1)
        self.add_task(1)
        self.succeeds("accept", "T2", "--verdict", "已验收")
        with (contract / "links.md").open("a") as output:
            output.write("坏映射\n")
        for task in ("T2", "T9"):
            with self.subTest(task=task):
                self.rejects("relink", task, "--source", "1", message=f"开放事项 {task} 不存在")
        self.rejects("relink", "T1", "--source", "1", "9", message="原话 9 不存在")


    def test_relink_rejects_invalid_task_number_before_writing(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "真实原话")
        (contract / "tasks" / "T0.md").write_text(
            'title: "错误编号事项"\ncriteria: "完成要求"\nstate: "在办"\npointer: "无"\n')
        links = contract / "links.md"
        before_links = links.read_bytes()
        before = self.files()
        code, output, errors = self.command("relink", "T0", "--source", "1")
        self.assertEqual(code, 1, output + errors)
        self.assertEqual(links.read_bytes(), before_links, "非法事项编号必须在写入之前拒绝")
        self.assertEqual(output, "")
        self.assertIn("T0", errors)
        self.assertEqual(self.files(), before)


    def test_relink_refuses_unreadable_links_or_quotes_without_writes(self):
        original_root = self.root
        for problem in ("missing-links", "invalid-links", "missing-quotes", "invalid-quote"):
            with self.subTest(problem=problem):
                self.root = original_root / problem
                self.root.mkdir()
                contract = self.open_goal("release")
                bad_path = self.make_contract_file_unreadable(contract, problem)
                self.rejects("relink", message=str(bad_path))


    def test_link_validation_errors_include_repair_hint(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "原要求")
        self.add_task(1)
        hint = "；用 relink [<事项> --source <编号>…] 修复，编号看 quotes/\n"
        cases = (("坏映射\n", "关系格式错误", None),
                 ("1 不必跟进 已答\n", "开放事项 T1 没有原话出处", "T1"))
        for links, message, task in cases:
            with self.subTest(message=message, links=links):
                (contract / "links.md").write_text(links)
                errors = self.rejects("check", message=message)
                if task is not None:
                    self.assertIn(f"tasks/{task}.md", errors)
                self.assertTrue(errors.endswith(hint), errors)
