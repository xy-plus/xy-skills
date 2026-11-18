import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from goal_test_support import *


class TrackAndVerifyTests(GoalTestSupport):

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

    def test_machine_cancel_quote_on_closed_task_remains_readable(self):
        contract = self.open_goal("release")
        self.add_closed_machine_cancel_quote(contract)

        self.assertIn("目标：交付新版本", self.succeeds("show"))
        self.assertIn("通过", self.succeeds("check"))

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

        saved = goal.Contract(contract, (contract / "goal.md").read_bytes())
        self.assertEqual(saved.done["T1"]["cancel_reason"], reason)
        self.assertNotIn("cancel_quote", saved.done["T1"])
        self.assertEqual(saved.links, {(1, "T1")})
        self.succeeds("reopen", "T1")
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
        self.succeeds("reopen", "T1")
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
        self.succeeds("reopen", "T1")
        self.assertEqual(goal.Contract(contract, (contract / "goal.md").read_bytes()).tasks["T1"], {
            "title": "发布", "criteria": "测试通过", "state": "在办", "pointer": "无",
        })
        self.assertNotIn("cancel_quote", (contract / "tasks" / "T1.md").read_text(encoding="utf-8"))
        self.assertEqual((contract / "links.md").read_bytes(), links)
        self.assertIn((2, "T1"), goal.Contract(contract, (contract / "goal.md").read_bytes()).links)
        self.succeeds("check")

    def test_reopen_rejects_open_task_without_writes(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布要求")
        self.add_task(1)
        self.rejects("reopen", "T1", message="已关闭")

    def test_reopen_rejects_closed_task_without_source_without_writes(self):
        contract = self.open_goal("release")
        done_task = contract / "done" / "T1.md"
        done_task.write_text(
            'title: "旧事项"\ncriteria: "原判据"\nstate: "在办"\npointer: "无"\n'
            'verdict: "已验收"\n', encoding="utf-8")
        original = done_task.read_bytes()
        self.rejects("reopen", "T1", message="已关闭事项 T1 没有出处，不能重开")
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
            "reopen", "T1",
            message="已关闭事项 T1 除取消依据外没有出处，不能重开",
        )
        self.assertEqual(done_task.read_bytes(), original)

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
        self.succeeds("close")
        del before[str((contract / "goal.md").relative_to(self.root))]
        self.assertEqual(self.files(), before)

    def test_show_lists_goal_and_unhandled_quotes_in_numeric_order(self):
        contract = self.open_goal("release")
        for number in (10, 2, 1):
            self.quote(contract, number, "不要漏掉要求")
        output = self.succeeds("show")
        from track_and_verify.operations import show
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

    def test_check_dismisses_withdrawn_prompt_from_real_record_shape(self):
        contract = self.open_goal("withdrawn")
        quote = contract / "quotes" / "1.md"
        quote.write_text("中性测试正文", encoding="utf-8")
        self.set_quote_mtime(quote, "2026-10-24T09:00:00.000Z", 0.2)
        before = quote.read_bytes(), quote.stat().st_mtime_ns
        self.write_transcript(self.withdrawn_transcript())

        self.succeeds("check")

        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"),
                         "1 不必跟进 自动：已撤回（会话记录 withdrawn）\n")
        self.assertEqual((quote.read_bytes(), quote.stat().st_mtime_ns), before)

    def test_session_reader_keeps_only_withdrawal_fields(self):
        from track_and_verify.operations import read_session_transcript
        self.write_transcript(self.withdrawn_transcript())

        records = read_session_transcript(self.session)

        withdrawn = next(record for record in records if record.get("uuid") == "withdrawn")
        self.assertEqual(withdrawn, {
            "uuid": "withdrawn",
            "parentUuid": "turn",
            "type": "user",
            "promptId": "withdrawn-prompt",
            "timestamp": "2026-10-24T09:00:00.000Z",
            "origin_kind": "human",
            "content": "中性测试正文",
        })
        self.assertTrue(all(set(record) <= {
            "uuid", "parentUuid", "type", "promptId", "timestamp", "origin_kind", "content",
        } for record in records))
        self.assertEqual(len(records), 7, "无 UUID 的附件、请求和队列记录不进入判定列表")

    def test_check_dismisses_withdrawn_prompt_with_non_assistant_attachment_child(self):
        contract = self.open_goal("withdrawn-attachment")
        quote = contract / "quotes" / "1.md"
        quote.write_text("中性测试正文", encoding="utf-8")
        self.set_quote_mtime(quote, "2026-10-24T09:00:00.000Z", 0.2)
        self.write_transcript(self.withdrawn_transcript(attachment=True))

        self.succeeds("check")

        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"),
                         "1 不必跟进 自动：已撤回（会话记录 withdrawn）\n")

    def test_check_does_not_dismiss_same_prompt_id_sibling(self):
        contract = self.open_goal("same-round")
        quote = contract / "quotes" / "1.md"
        quote.write_text("中性测试正文", encoding="utf-8")
        self.set_quote_mtime(quote, "2026-10-24T09:00:00.000Z", 0.2)
        records = self.withdrawn_transcript()
        withdrawn_index = next(index for index, record in enumerate(records)
                               if record.get("uuid") == "withdrawn")
        records.insert(withdrawn_index + 1, self.transcript_record(
            "user", uuid="same-round-result", parent="turn",
            timestamp="2026-10-24T09:00:20.000Z", prompt_id="withdrawn-prompt",
            content=[{"type": "tool_result", "content": "中性工具结果"}]))
        records.insert(withdrawn_index + 2, self.transcript_record(
            "user", uuid="same-round-text", parent="same-round-result",
            timestamp="2026-10-24T09:00:21.000Z", prompt_id="withdrawn-prompt",
            content=[{"type": "text", "text": "中性插话"}]))
        records.insert(withdrawn_index + 3, self.transcript_record(
            "assistant", uuid="same-round-answer", parent="same-round-text",
            timestamp="2026-10-24T09:00:30.000Z", role="assistant"))
        next(record for record in records if record.get("uuid") == "away")["parentUuid"] = "other-turn"
        self.write_transcript(records)

        code, output, errors = self.command("check")

        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("原话 1 还没处理", errors)
        self.assertNotIn("提示：未做撤回对账", errors)
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"), "")

    def test_check_does_not_dismiss_message_with_an_assistant_descendant(self):
        contract = self.open_goal("answered")
        quote = contract / "quotes" / "1.md"
        quote.write_text("中性测试正文", encoding="utf-8")
        self.set_quote_mtime(quote, "2026-10-24T09:00:00.000Z", 0.2)
        records = self.withdrawn_transcript()
        records.insert(5, self.transcript_record(
            "assistant", uuid="answer", parent="withdrawn", timestamp="2026-10-24T09:00:10.000Z"))
        self.write_transcript(records)

        code, output, errors = self.command("check")

        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("原话 1 还没处理", errors)
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"), "")

    def test_check_keeps_ancestor_quote_pending_when_nested_user_message_is_answered(self):
        contract = self.open_goal("nested-answer")
        first = contract / "quotes" / "1.md"
        second = contract / "quotes" / "2.md"
        first.write_text("中性父消息", encoding="utf-8")
        second.write_text("中性子消息", encoding="utf-8")
        self.set_quote_mtime(first, "2026-10-24T09:00:00.000Z", 5)
        self.set_quote_mtime(second, "2026-10-24T09:00:00.000Z", 20)
        records = self.withdrawn_transcript(content="中性父消息")
        withdrawn_index = next(index for index, record in enumerate(records)
                               if record.get("uuid") == "withdrawn")
        records.insert(withdrawn_index + 1, self.transcript_record(
            "user", uuid="nested-user", parent="withdrawn", timestamp="2026-10-24T09:00:15.000Z",
            prompt_id="nested-prompt", origin="human", content="中性子消息"))
        records.insert(withdrawn_index + 2, self.transcript_record(
            "assistant", uuid="nested-answer", parent="nested-user",
            timestamp="2026-10-24T09:00:16.000Z", role="assistant"))
        self.write_transcript(records)

        code, output, errors = self.command("check")

        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("原话 1 还没处理", errors)
        self.assertIn("原话 2 还没处理", errors)
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"), "")

    def test_check_does_not_match_an_old_quote_when_hook_missed_withdrawn_message(self):
        contract = self.open_goal("old-quote")
        quote = contract / "quotes" / "1.md"
        quote.write_text("中性测试正文", encoding="utf-8")
        self.set_quote_mtime(quote, "2026-10-24T08:00:00.000Z")
        self.write_transcript(self.withdrawn_transcript())

        code, output, errors = self.command("check")

        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("原话 1 还没处理", errors)
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"), "")

    def test_check_matches_only_quote_written_between_withdrawn_and_resent_prompts(self):
        contract = self.open_goal("resent")
        first = contract / "quotes" / "1.md"
        second = contract / "quotes" / "2.md"
        first.write_text("中性测试正文", encoding="utf-8")
        second.write_text("中性测试正文", encoding="utf-8")
        self.set_quote_mtime(first, "2026-10-24T09:00:00.000Z", 0.2)
        self.set_quote_mtime(second, "2026-10-24T09:02:00.000Z", 0.2)
        records = self.withdrawn_transcript()
        resent = self.transcript_record(
            "user", uuid="resent", parent="turn", timestamp="2026-10-24T09:02:00.000Z",
            prompt_id="resent-prompt", origin="human", content="中性测试正文")
        records.insert(-1, resent)
        records.insert(-1, self.transcript_record(
            "assistant", uuid="resent-answer", parent="resent", timestamp="2026-10-24T09:02:10.000Z"))
        records[-1]["timestamp"] = "2026-10-24T09:03:00.000Z"
        self.write_transcript(records)

        code, output, errors = self.command("check")

        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("原话 2 还没处理", errors)
        self.assertNotIn("原话 1 还没处理", errors)
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"),
                         "1 不必跟进 自动：已撤回（会话记录 withdrawn）\n")

    def test_check_does_not_guess_between_multiple_quotes_in_the_same_time_window(self):
        contract = self.open_goal("ambiguous")
        for number, offset in ((1, 0.2), (2, 0.4)):
            quote = contract / "quotes" / f"{number}.md"
            quote.write_text("中性测试正文", encoding="utf-8")
            self.set_quote_mtime(quote, "2026-10-24T09:00:00.000Z", offset)
        self.write_transcript(self.withdrawn_transcript())

        code, output, errors = self.command("check")

        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("原话 1 还没处理", errors)
        self.assertIn("原话 2 还没处理", errors)
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"), "")

    def test_check_leaves_already_handled_withdrawn_quote_unchanged(self):
        for handled_by in ("task", "dismissal"):
            with self.subTest(handled_by=handled_by):
                self.use_session(f"session-{handled_by}")
                contract = self.open_goal(handled_by)
                self.quote(contract, 1, "中性测试正文")
                self.quote(contract, 2, "另一条中性正文")
                self.set_quote_mtime(contract / "quotes" / "1.md",
                                     "2026-10-24T09:00:00.000Z", 0.2)
                if handled_by == "task":
                    self.add_task(1)
                else:
                    self.succeeds("dismiss", "1", "--reason", "已经处理")
                before = (contract / "links.md").read_bytes()
                self.write_transcript(self.withdrawn_transcript())

                code, output, errors = self.command("check")

                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertIn("原话 2 还没处理", errors)
                self.assertEqual((contract / "links.md").read_bytes(), before)

    def test_check_does_not_dismiss_nonhuman_or_meta_user_records(self):
        for origin, is_meta in ((None, True), ("notification", False)):
            with self.subTest(origin=origin, is_meta=is_meta):
                self.use_session("session-meta" if is_meta else "session-notification")
                contract = self.open_goal("nonhuman-meta" if is_meta else "nonhuman-notification")
                quote = contract / "quotes" / "1.md"
                quote.write_text("中性测试正文", encoding="utf-8")
                self.set_quote_mtime(quote, "2026-10-24T09:00:00.000Z", 0.2)
                records = self.withdrawn_transcript()
                withdrawn = next(record for record in records
                                 if record.get("uuid") == "withdrawn")
                if origin is None:
                    withdrawn.pop("origin")
                    withdrawn["isMeta"] = is_meta
                else:
                    withdrawn["origin"] = {"kind": origin}
                self.write_transcript(records)

                code, output, errors = self.command("check")

                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertIn("原话 1 还没处理", errors)
                self.assertEqual((contract / "links.md").read_text(encoding="utf-8"), "")

    def test_check_does_not_read_session_record_when_nothing_is_pending(self):
        self.open_goal("no-pending")
        self.transcript_path().unlink()

        self.assertIn("通过", self.succeeds("check"))

    def test_check_reports_record_lookup_parse_and_utf8_errors_without_marking(self):
        cases = ("missing", "multiple", "bad-json", "bad-utf8", "read-error")
        for problem in cases:
            with self.subTest(problem=problem):
                self.use_session(f"session-{problem}")
                contract = self.open_goal(problem)
                quote = contract / "quotes" / "1.md"
                quote.write_text("中性测试正文", encoding="utf-8")
                self.set_quote_mtime(quote, "2026-10-24T09:00:00.000Z", 0.2)
                transcript = self.write_transcript(self.withdrawn_transcript())
                if problem == "missing":
                    transcript.unlink()
                elif problem == "multiple":
                    self.write_transcript([], session=self.session, project_name="-another-project")
                elif problem == "bad-json":
                    transcript.write_bytes(transcript.read_bytes().replace(
                        b'{"type": "api-request"', b'{not-json', 1))
                elif problem == "bad-utf8":
                    transcript.write_bytes(transcript.read_bytes().replace(
                        b'{"type": "api-request"', b'\xff', 1))

                if problem == "read-error":
                    open_file = Path.open

                    def fail_transcript(path, *arguments, **keywords):
                        if path == transcript:
                            raise OSError("记录不可读")
                        return open_file(path, *arguments, **keywords)

                    with mock.patch.object(Path, "open", fail_transcript):
                        code, output, errors = self.command("check")
                else:
                    code, output, errors = self.command("check")

                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertIn("提示：未做撤回对账：", errors)
                self.assertIn("原话 1 还没处理", errors)
                self.assertEqual((contract / "links.md").read_text(encoding="utf-8"), "")

    def test_check_ignores_incomplete_final_transcript_line(self):
        contract = self.open_goal("incomplete-tail")
        quote = contract / "quotes" / "1.md"
        quote.write_text("中性测试正文", encoding="utf-8")
        self.set_quote_mtime(quote, "2026-10-24T09:00:00.000Z", 0.2)
        transcript = self.write_transcript(self.withdrawn_transcript())
        transcript.write_bytes(transcript.read_bytes() + b'{"unfinished":')

        self.succeeds("check")

        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"),
                         "1 不必跟进 自动：已撤回（会话记录 withdrawn）\n")

    def test_check_does_not_validate_another_sessions_tasks(self):
        first = self.open_goal("release")
        other = self.project / "20261024-093001-other" / "xy-goal"
        shutil.copytree(first, other)
        metadata = (other / "goal.md").read_text(encoding="utf-8")
        (other / "goal.md").write_text(metadata.replace("session-main", "session-other"),
                                      encoding="utf-8")
        (other / "tasks" / "T1.md").write_text("损坏", encoding="utf-8")
        self.assertIn("通过", self.succeeds("check"))

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
        original = (contract / "tasks" / "T1.md").read_bytes()
        self.succeeds("accept", "T1", "--verdict", "python3 -m unittest，退出码 0")
        self.assertFalse((contract / "tasks" / "T1.md").exists())
        closed = (contract / "done" / "T1.md").read_bytes()
        self.assertTrue(closed.startswith(original))
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
