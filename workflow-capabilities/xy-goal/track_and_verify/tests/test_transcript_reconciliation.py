import shutil
from pathlib import Path

from track_test_support import GoalTestSupport, mock
from track_and_verify.operations import read_session_transcript


class TranscriptReconciliationTests(GoalTestSupport):

    def _assert_pending_quotes(self, contract, quote_numbers):
        code, output, errors = self.command("check")
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        for number in quote_numbers:
            self.assertIn(f"原话 {number} 还没处理", errors)
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"), "")
        return errors

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
            content=[{"type": "tool_result", "content": "中性工具结果"}], origin=None, subtype=None, is_meta=False, role="user"))
        records.insert(withdrawn_index + 2, self.transcript_record(
            "user", uuid="same-round-text", parent="same-round-result",
            timestamp="2026-10-24T09:00:21.000Z", prompt_id="withdrawn-prompt",
            content=[{"type": "text", "text": "中性插话"}], origin=None, subtype=None, is_meta=False, role="user"))
        records.insert(withdrawn_index + 3, self.transcript_record(
            "assistant", uuid="same-round-answer", parent="same-round-text",
            timestamp="2026-10-24T09:00:30.000Z", role="assistant", prompt_id=None, origin=None, content=None, subtype=None, is_meta=False))
        next(record for record in records if record.get("uuid") == "away")["parentUuid"] = "other-turn"
        self.write_transcript(records)

        errors = self._assert_pending_quotes(contract, (1,))
        self.assertNotIn("提示：未做撤回对账", errors)


    def test_check_does_not_dismiss_message_with_an_assistant_descendant(self):
        contract = self.open_goal("answered")
        quote = contract / "quotes" / "1.md"
        quote.write_text("中性测试正文", encoding="utf-8")
        self.set_quote_mtime(quote, "2026-10-24T09:00:00.000Z", 0.2)
        records = self.withdrawn_transcript()
        records.insert(5, self.transcript_record(
            "assistant", uuid="answer", parent="withdrawn", timestamp="2026-10-24T09:00:10.000Z", prompt_id=None, origin=None, content=None, subtype=None, is_meta=False, role="user"))
        self.write_transcript(records)

        self._assert_pending_quotes(contract, (1,))


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
            prompt_id="nested-prompt", origin="human", content="中性子消息", subtype=None, is_meta=False, role="user"))
        records.insert(withdrawn_index + 2, self.transcript_record(
            "assistant", uuid="nested-answer", parent="nested-user",
            timestamp="2026-10-24T09:00:16.000Z", role="assistant", prompt_id=None, origin=None, content=None, subtype=None, is_meta=False))
        self.write_transcript(records)

        self._assert_pending_quotes(contract, (1, 2))


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
            prompt_id="resent-prompt", origin="human", content="中性测试正文", subtype=None, is_meta=False, role="user")
        records.insert(-1, resent)
        records.insert(-1, self.transcript_record(
            "assistant", uuid="resent-answer", parent="resent", timestamp="2026-10-24T09:02:10.000Z", prompt_id=None, origin=None, content=None, subtype=None, is_meta=False, role="user"))
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

        self._assert_pending_quotes(contract, (1, 2))


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
