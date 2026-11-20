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


class TaskLifecycleTests(GoalTestSupport):

    def test_add_refuses_both_known_machine_input_shapes_as_sources(self):
        original_root = self.root
        cases = (
            ("timer", lambda contract: f"本次契约：{contract}。按 xy-goal 的「定时自查」一节做",
             "自动：本契约定时提示"),
            ("notification",
             lambda contract: "<task-notification><task-id>13</task-id><status>completed</status>"
                              "<summary>运行测试</summary></task-notification>",
             "自动：完整后台任务通知"),
        )
        for name, text_for, reason in cases:
            with self.subTest(name=name):
                self.root = original_root / name
                self.root.mkdir()
                contract = self.open_goal("release")
                self.quote(contract, 1, text_for(contract))
                self.rejects("add", "--title", "发布", "--criteria", "测试通过",
                             "--source-kind", "原话直提",
                             "--source", "1", "--state", "在办", "--pointer", "无",
                             message=f"原话 1 是机器输入（{reason}）")


    def test_update_source_refuses_machine_input_without_writing(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.add_task(1)
        machine_text = f"本次契约：{contract}。按 xy-goal 的「定时自查」一节做"
        self.quote(contract, 2, machine_text)
        self.rejects("update", "T1", "--source", "2",
                     message="原话 2 是机器输入（自动：本契约定时提示）")
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"), "1 T1\n")


    def test_machine_cancel_quote_on_closed_task_remains_readable(self):
        contract = self.open_goal("release")
        self.add_closed_machine_cancel_quote(contract)

        self.assertIn("目标：交付新版本", self.succeeds("show"))
        self.assertIn("通过", self.succeeds("check"))


    def test_cancel_quote_refuses_machine_input(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.add_task(1)
        self.quote(contract, 2, f"本次契约：{contract}。按 xy-goal 的「定时自查」一节做")

        self.rejects("cancel", "T1", "--quote", "2",
                     message="原话 2 是机器输入（自动：本契约定时提示）")


    def test_author_initiated_add_stores_evidence_and_show_displays_it(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "用户要求按期发布。")
        evidence = "不做会延误交付；做了能按期发布；代价是两小时"

        output = self.succeeds("add", "--title", "整理发布流程", "--criteria", "流程验收通过",
                               "--source", "1", "--source-kind", "作者发起", "--evidence", evidence,
                               "--state", "在办", "--pointer", "无")

        self.assertIn("T1", output)
        saved = goal.Contract(contract, (contract / "goal.md").read_bytes())
        self.assertEqual(saved.tasks["T1"]["evidence"], evidence)
        self.assertEqual(saved.links, {(1, "T1")})
        self.assertIn(f"出处：原话 1；作者发起：{evidence}", self.succeeds("show"))
        self.succeeds("check")


    def test_add_requires_a_quote_source_even_with_evidence(self):
        self.open_goal("release")

        self.rejects_argument_error(
            "add", "--title", "整理发布流程", "--criteria", "流程验收通过",
            "--source-kind", "作者发起",
            "--evidence", "不做会延误；做了能发布；代价两小时",
            "--state", "在办", "--pointer", "无",
            message="--source",
        )


    def test_evidence_item_can_receive_an_additional_quote_source(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "用户要求按期发布。")
        evidence = "不做会延误交付；做了能按期发布；代价是两小时"
        self.succeeds("add", "--title", "整理发布流程", "--criteria", "流程验收通过",
                      "--source", "1", "--source-kind", "作者发起", "--evidence", evidence,
                      "--state", "在办", "--pointer", "无")
        self.quote(contract, 2, "用户要求验收后发布。")

        self.succeeds("update", "T1", "--source", "2")

        saved = goal.Contract(contract, (contract / "goal.md").read_bytes())
        self.assertEqual(saved.tasks["T1"]["evidence"], evidence)
        self.assertEqual(saved.links, {(1, "T1"), (2, "T1")})
        self.assertIn(f"出处：原话 1、2；作者发起：{evidence}", self.succeeds("show"))
        self.succeeds("check")


    def test_evidence_and_quote_item_can_be_cancelled_with_a_reason(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "用户要求按期发布。")
        evidence = "从原话延伸：不做会延误；做了能按期发布；代价两小时"
        self.succeeds(
            "add", "--title", "整理发布流程", "--criteria", "流程验收通过",
            "--source", "1", "--source-kind", "作者发起", "--evidence", evidence,
            "--state", "在办", "--pointer", "无",
        )

        reason = "决定暂缓，等待发布计划确认"
        self.succeeds("cancel", "T1", "--reason", reason)

        archive_text = (contract / "done" / "T1.md").read_text(encoding="utf-8")
        self.assertNotRegex(archive_text, r"(?m)^(?:state|pointer): ")
        saved = goal.Contract(contract, (contract / "goal.md").read_bytes())
        self.assertEqual(saved.done["T1"]["cancel_reason"], reason)
        self.assertEqual(saved.done["T1"], {
            "title": "整理发布流程", "criteria": "流程验收通过", "evidence": evidence,
            "cancel_reason": reason,
        })
        self.assertNotIn("cancel_quote", saved.done["T1"])
        self.assertEqual(saved.links, {(1, "T1")})
        self.succeeds("reopen", "T1", "--state", "在办", "--pointer", "无")
        reopened = goal.Contract(contract, (contract / "goal.md").read_bytes())
        self.assertEqual(reopened.tasks["T1"]["evidence"], evidence)
        self.assertEqual(reopened.links, {(1, "T1")})
        self.succeeds("check")


    def test_open_task_without_quote_source_is_rejected_when_loaded(self):
        contract = self.open_goal("release")
        (contract / "tasks" / "T1.md").write_text(
            'title: "整理发布流程"\ncriteria: "流程验收通过"\nstate: "在办"\n'
            'pointer: "无"\nevidence: "从原话延伸出的作者证据"\n',
            encoding="utf-8",
        )

        self.rejects("show", message="开放事项 T1 没有原话出处")


    def test_task_without_evidence_still_requires_a_quote_to_cancel(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "用户要求按期发布。")
        self.quote(contract, 2, "用户要求取消发布。")
        self.add_task(1)

        self.rejects("cancel", "T1", "--reason", "决定暂缓",
                     message="没有作者发起证据的事项必须用 --quote")
        self.succeeds("cancel", "T1", "--quote", "2")

        closed = goal.Contract(contract, (contract / "goal.md").read_bytes()).done["T1"]
        self.assertEqual(closed["cancel_quote"], "2")
        self.assertNotIn("cancel_reason", closed)
        self.succeeds("check")


    def test_dismiss_rejects_quote_linked_to_closed_task(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.quote(contract, 2, "取消依据")
        self.add_task(1)
        self.succeeds("cancel", "T1", "--quote", "2")
        self.rejects("dismiss", "2", "--reason", "已答", message="原话 2 已挂事项 T1")


    def test_update_source_removes_existing_dismissal_without_read_error(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.quote(contract, 2, "再加一个发布要求")
        self.add_task(1)
        self.succeeds("dismiss", "2", "--reason", "暂不跟进")

        self.succeeds("update", "T1", "--source", "2")

        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"),
                         "1 T1\n2 T1\n")
        self.succeeds("check")


    def test_reopen_accepted_task_discards_old_verdict_and_allows_new_acceptance(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.add_task(1)
        self.succeeds("accept", "T1", "--verdict", "旧结论不成立")
        links = (contract / "links.md").read_bytes()
        self.succeeds("reopen", "T1", "--state", "在办", "--pointer", "无")
        self.assertEqual(goal.Contract(contract, (contract / "goal.md").read_bytes()).tasks["T1"], {
            "title": "发布", "criteria": "测试通过", "state": "在办", "pointer": "无",
        })
        self.assertFalse((contract / "done" / "T1.md").exists())
        self.assertEqual((contract / "links.md").read_bytes(), links)
        self.assertIn("T1 发布", self.succeeds("show"))
        self.succeeds("accept", "T1", "--verdict", "新结论成立")
        closed = (contract / "done" / "T1.md").read_text(encoding="utf-8")
        self.assertEqual(goal.Contract(contract, (contract / "goal.md").read_bytes()).done["T1"]["verdict"], "新结论成立")
        self.assertIn("新结论成立", closed)
        self.assertNotIn("旧结论不成立", closed)


    def test_reopen_cancelled_task_keeps_cancellation_quote_linked(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.quote(contract, 2, "取消依据")
        self.add_task(1)
        self.succeeds("cancel", "T1", "--quote", "2")
        links = (contract / "links.md").read_bytes()
        self.succeeds("reopen", "T1", "--state", "等用户：确认需求", "--pointer", "等待决定")
        self.assertEqual(goal.Contract(contract, (contract / "goal.md").read_bytes()).tasks["T1"], {
            "title": "发布", "criteria": "测试通过", "state": "等用户：确认需求",
            "pointer": "等待决定",
        })
        self.assertNotIn("cancel_quote", (contract / "tasks" / "T1.md").read_text(encoding="utf-8"))
        self.assertEqual((contract / "links.md").read_bytes(), links)
        self.assertIn((2, "T1"), goal.Contract(contract, (contract / "goal.md").read_bytes()).links)
        self.succeeds("check")


    def test_reopen_rejects_open_task_without_writes(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.add_task(1)
        self.rejects("reopen", "T1", "--state", "在办", "--pointer", "无", message="已关闭")


    def test_reopen_rejects_closed_task_without_source_without_writes(self):
        contract = self.open_goal("release")
        done_task = contract / "done" / "T1.md"
        done_task.write_text(
            'title: "旧事项"\ncriteria: "原判据"\nstate: "在办"\npointer: "无"\n'
            'verdict: "已验收"\n', encoding="utf-8")
        original = done_task.read_bytes()
        self.rejects("reopen", "T1", "--state", "在办", "--pointer", "无",
                     message="已关闭事项 T1 没有出处，不能重开")
        self.assertEqual(done_task.read_bytes(), original)


    def test_reopen_rejects_cancelled_task_without_non_cancellation_source(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "取消依据")
        done_task = contract / "done" / "T1.md"
        done_task.write_text(
            'title: "旧事项"\ncriteria: "原判据"\nstate: "在办"\npointer: "无"\n'
            'cancel_quote: "1"\n', encoding="utf-8")
        (contract / "links.md").write_text("1 T1\n", encoding="utf-8")
        original = done_task.read_bytes()
        self.rejects(
            "reopen", "T1", "--state", "在办", "--pointer", "无",
            message="已关闭事项 T1 除取消依据外没有出处，不能重开",
        )
        self.assertEqual(done_task.read_bytes(), original)


    def test_add_keeps_many_to_many_sources_only_in_links(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "测试后发布")
        self.quote(contract, 2, "使用新流程")
        self.assertIn("T1", self.add_task(1, 2))
        self.assertIn("T2", self.add_task(1))
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"),
                         "1 T1\n1 T2\n2 T1\n")
        output = self.succeeds("show")
        self.assertIn("T1 发布\n  判据：测试通过\n  出处：原话 1、2\n  状态：在办；指针：无", output)
        self.assertIn("还没处理的原话：无", output)
        task_values = goal.Contract(contract, (contract / "goal.md").read_bytes()).tasks["T1"]
        self.assertNotIn("evidence", task_values)
        self.assertIn("通过", self.succeeds("check"))


    def test_add_refuses_missing_source_and_invalid_state_before_writing(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "完成发布")
        base = ("add", "--title", "发布", "--criteria", "通过", "--pointer", "无",
                "--source-kind", "原话直提")
        self.rejects(*base, "--source", "1", "9", "--state", "在办", message="原话 9 不存在")
        for state in ("完成", "等用户：", "等：T1", "在跑：worker"):
            with self.subTest(state=state):
                self.rejects(*base, "--source", "1", "--state", state, message="状态")


    def test_update_multiple_fields_preserves_quotes_and_changes_show(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.quote(contract, 2, "必须两轮通过")
        self.add_task(1)
        before_quotes = {p.name: p.read_bytes() for p in (contract / "quotes").iterdir()}
        self.succeeds("update", "T1", "--title", "新发布", "--criteria", "两轮通过",
                      "--state", "等用户：确认时间", "--pointer", "/tmp/report", "--source", "2")
        output = self.succeeds("show")
        for value in ("新发布", "两轮通过", "等用户：确认时间", "/tmp/report", "原话 1、2"):
            self.assertIn(value, output)
        self.assertEqual({p.name: p.read_bytes() for p in (contract / "quotes").iterdir()}, before_quotes)


    def test_update_source_only_adds_relations_without_replacing_old_sources(self):
        contract = self.open_goal("release")
        for number in (1, 2, 3):
            self.quote(contract, number, "要求")
        self.add_task(1)
        before_task = (contract / "tasks" / "T1.md").read_bytes()
        self.succeeds("update", "T1", "--source", "2", "3", "2")
        self.assertEqual((contract / "tasks" / "T1.md").read_bytes(), before_task)
        self.assertEqual((contract / "links.md").read_text(), "1 T1\n2 T1\n3 T1\n")


    def test_update_refuses_legacy_options_missing_source_and_missing_task(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "要求")
        self.add_task(1)
        cases = (
            (("T1", "--criteria", "变更"), "--source"),
            (("T1", "--state", "等用户："), "状态"),
            (("T1",), "至少改一项"),
            (("T9", "--source", "1"), "T9"),
            (("T1", "--title", "发布", "--source", "9"), "原话 9 不存在"),
        )
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                self.rejects("update", *arguments, message=message)


    def test_add_requires_source_kind_and_evidence_for_author_initiated_items(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布")
        arguments = ("--title", "补充发布检查", "--criteria", "复核通过",
                     "--source", "1", "--state", "在办", "--pointer", "无")

        self.rejects_argument_error("add", *arguments, message="--source-kind")
        self.rejects("add", *arguments, "--source-kind", "作者发起", message="--evidence")
        self.rejects("add", *arguments, "--source-kind", "作者发起", "--evidence", " \t",
                     message="证据不能为空")
        self.rejects("add", *arguments, "--source-kind", "作者发起", "--evidence", "无",
                     message="作者发起证据不能写「无」")

        self.succeeds("add", *arguments, "--source-kind", "作者发起",
                      "--evidence", "由发布要求补出复核步骤及其依据")
        task = (contract / "tasks" / "T1.md").read_text(encoding="utf-8")
        self.assertIn('evidence: "由发布要求补出复核步骤及其依据"', task)
        self.assertIn("作者发起：由发布要求补出复核步骤及其依据", self.succeeds("show"))

        self.succeeds("add", "--title", "原话直提", "--criteria", "发布",
                      "--source-kind", "原话直提", "--source", "1",
                      "--state", "在办", "--pointer", "无")
        direct_task = (contract / "tasks" / "T2.md").read_text(encoding="utf-8")
        self.assertNotIn("evidence:", direct_task)


    def test_dismiss_handles_quote_without_touching_original_text(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "这个问题请回答\n\n保留原文。\n")
        before = (contract / "quotes" / "1.md").read_bytes()
        self.succeeds("dismiss", "1", "--reason", "问题已答")
        self.assertEqual((contract / "links.md").read_text(), "1 不必跟进 问题已答\n")
        self.assertEqual((contract / "quotes" / "1.md").read_bytes(), before)
        self.assertIn("还没处理的原话：无", self.succeeds("show"))
        self.succeeds("check")
        self.rejects("dismiss", "8", "--reason", "已答", message="原话 8 不存在")
        self.rejects("dismiss", "1", "--reason", "", message="原因")
        self.rejects("dismiss", "1", "--reason", "答过\n2 T1", message="一行")


    def test_accept_preserves_task_and_adds_verdict_without_reusing_id(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "完成发布")
        self.add_task(1)
        self.succeeds("accept", "T1", "--verdict", "python3 -m unittest，退出码 0")
        self.assertFalse((contract / "tasks" / "T1.md").exists())
        closed = (contract / "done" / "T1.md").read_bytes()
        self.assertEqual(
            closed,
            'title: "发布"\ncriteria: "测试通过"\n'
            'verdict: "python3 -m unittest，退出码 0"\n'.encode("utf-8"),
        )
        self.assertIn("退出码 0", closed.decode())
        self.assertIn("T2", self.add_task(1))
        self.assertNotIn("T1 发布", self.succeeds("show"))
        self.rejects("accept", "T1", "--verdict", "重复", message="开放事项")
        self.rejects("accept", "T2", "--verdict", "", message="验收结论")


    def test_cancel_requires_existing_quote_and_links_cancellation_reason(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布")
        self.quote(contract, 2, "取消发布")
        self.add_task(1)
        self.rejects("cancel", "T1", "--quote", "9", message="原话 9 不存在")
        self.succeeds("cancel", "T1", "--quote", "2")
        self.assertFalse((contract / "tasks" / "T1.md").exists())
        self.assertIn("2", (contract / "done" / "T1.md").read_text())
        self.assertEqual((contract / "links.md").read_text(), "1 T1\n2 T1\n")
        self.succeeds("check")


    def test_dismiss_rejects_all_record_line_breaks_before_writing(self):
        original_root = self.root
        for separator in ("\u2028", "\u2029", "\u0085", "\v", "\f", "\x1c", "\x1d", "\x1e"):
            with self.subTest(separator=repr(separator)):
                self.root = original_root / str(ord(separator))
                self.root.mkdir()
                contract = self.open_goal("release")
                self.quote(contract, 1, "问题已回答")
                self.rejects("dismiss", "1", "--reason", "甲" + separator + "乙", message="一行")


    def test_failed_mapping_write_rolls_back_task_update(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布")
        self.quote(contract, 2, "加严判据")
        self.add_task(1)
        real_replace = os.replace
        def cannot_replace_links(source, destination):
            if Path(destination) == contract / "links.md":
                raise PermissionError("模拟映射文件写入失败")
            return real_replace(source, destination)
        with mock.patch("goal.os.replace", side_effect=cannot_replace_links):
            self.rejects("update", "T1", "--criteria", "两轮通过",
                         "--source", "2", message="模拟映射文件写入失败")


    def test_accept_archive_omits_state_and_pointer_but_keeps_evidence(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        evidence = "不做会延误；完成可交付；代价两小时"
        self.succeeds(
            "add", "--title", "整理发布流程", "--criteria", "流程验收通过",
            "--source", "1", "--source-kind", "作者发起", "--evidence", evidence,
            "--state", "在办", "--pointer", "终审中",
        )

        self.succeeds("accept", "T1", "--verdict", "按判据验收通过")

        archive_text = (contract / "done" / "T1.md").read_text(encoding="utf-8")
        self.assertNotRegex(archive_text, r"(?m)^(?:state|pointer): ")
        saved = goal.Contract(contract, (contract / "goal.md").read_bytes())
        self.assertEqual(saved.done["T1"], {
            "title": "整理发布流程", "criteria": "流程验收通过", "evidence": evidence,
            "verdict": "按判据验收通过",
        })


    def test_cancel_quote_archive_omits_state_and_pointer(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.quote(contract, 2, "取消发布")
        self.add_task(1)

        self.succeeds("cancel", "T1", "--quote", "2")

        archive_text = (contract / "done" / "T1.md").read_text(encoding="utf-8")
        self.assertNotRegex(archive_text, r"(?m)^(?:state|pointer): ")
        saved = goal.Contract(contract, (contract / "goal.md").read_bytes())
        self.assertEqual(saved.done["T1"], {
            "title": "发布", "criteria": "测试通过", "cancel_quote": "2",
        })


    def test_old_done_state_and_pointer_are_ignored_and_reopen_uses_arguments(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "旧事项要求")
        self.quote(contract, 2, "另一个事项要求")
        evidence = "由原话延伸；不做会延误；完成可交付；代价两小时"
        old_done = contract / "done" / "T1.md"
        old_done.write_text(
            'title: "旧事项"\ncriteria: "旧判据"\nstate: "已关闭"\npointer: ""\n'
            f'evidence: {json.dumps(evidence, ensure_ascii=False)}\n'
            'verdict: "旧结论"\n', encoding="utf-8")
        (contract / "links.md").write_text("1 T1\n", encoding="utf-8")
        old_done_bytes = old_done.read_bytes()

        self.add_task(2)
        self.assertIn("T2 发布", self.succeeds("show"))
        self.succeeds("check")
        self.succeeds("accept", "T2", "--verdict", "新事项验收通过")
        self.succeeds("check")
        self.assertEqual(old_done.read_bytes(), old_done_bytes)

        links = (contract / "links.md").read_bytes()
        self.succeeds("reopen", "T1", "--state", "等用户：部署窗口", "--pointer", "/tmp/deploy")

        reopened = goal.Contract(contract, (contract / "goal.md").read_bytes())
        self.assertEqual(reopened.tasks["T1"], {
            "title": "旧事项", "criteria": "旧判据", "state": "等用户：部署窗口",
            "pointer": "/tmp/deploy", "evidence": evidence,
        })
        self.assertFalse(old_done.exists())
        self.assertEqual((contract / "links.md").read_bytes(), links)
        self.succeeds("check")


    def test_reopen_rejects_invalid_state_and_empty_pointer_without_writes(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.add_task(1)
        self.succeeds("accept", "T1", "--verdict", "验收通过")

        self.rejects("reopen", "T1", "--state", "已完成", "--pointer", "无",
                     message="状态只认")
        self.rejects("reopen", "T1", "--state", "在办", "--pointer", "",
                     message="指针不能为空")


    def test_reopen_requires_state_and_pointer_arguments(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.add_task(1)
        self.succeeds("accept", "T1", "--verdict", "验收通过")

        self.rejects_argument_error("reopen", "T1", "--pointer", "/tmp/release", message="--state")
        self.rejects_argument_error("reopen", "T1", "--state", "在办", message="--pointer")
