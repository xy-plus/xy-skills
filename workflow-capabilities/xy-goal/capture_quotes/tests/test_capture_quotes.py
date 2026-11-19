import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from goal_test_support import (
    GoalTestSupport,
    SCRIPT_DIRECTORY,
    ThreadPoolExecutor,
    goal,
    json,
    mock,
    shutil,
    subprocess,
)
from goal_common import quote_files, timer_prompt


class CaptureQuotesTests(GoalTestSupport):

    def test_inbox_hooks_reject_symlink_project_without_writing_outside(self):
        outside = self.replace_project_with_symlink()
        for operation, fields in (
            ("hook-prompt", {"prompt": "不要写到 runs 外面"}),
            ("hook-answer", {"tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": "继续"}),
        ):
            with self.subTest(operation=operation):
                before = self.directory_entries(outside)
                payload = {"session_id": self.session,
                           "transcript_path": str(self.projects / self.project_name / f"{self.session}.jsonl"),
                           **fields}
                code, output, errors = self.run_goal(
                    operation, session=None, pid=None, stdin=json.dumps(payload),
                )
                self.assert_hook_error((code, output, errors), operation,
                                       message="项目目录", record="这次输入没有记入契约。")
                self.assertEqual(self.directory_entries(outside), before)

    def test_both_hooks_use_transcript_parent_for_inbox_without_session_lookup(self):
        for operation, fields in (
            ("hook-prompt", {"prompt": "来自记录路径"}),
            ("hook-answer", {"tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": "继续"}),
        ):
            with self.subTest(operation=operation):
                session = operation + "-session"
                self.hook(operation, {"session_id": session,
                                      "transcript_path": f"/unused/-hook-project/{session}.jsonl", **fields})
                quote = self.root / "-hook-project" / "xy-goal-inbox" / session / "1.md"
                self.assertTrue(quote.is_file())
                self.assertFalse(self.project.exists())
                if operation == "hook-prompt":
                    self.assertEqual(quote.read_text(), "来自记录路径")
                else:
                    self.assertEqual(json.loads(quote.read_text())["tool_response"], "继续")

    def test_hook_without_transcript_cannot_create_inbox(self):
        for operation, fields in (
            ("hook-prompt", {"prompt": "原话"}),
            ("hook-answer", {"tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": {}}),
        ):
            with self.subTest(operation=operation):
                before = list(self.root.rglob("*"))
                code, output, errors = self.run_goal(
                    operation, session=None, pid=None,
                    stdin=json.dumps({"session_id": self.session, **fields}),
                )
                self.assert_hook_error((code, output, errors), operation,
                                       message="transcript_path", record="这次输入没有记入契约。")
                self.assertEqual(list(self.root.rglob("*")), before)

    def test_hook_prompt_writes_exact_bytes_and_keeps_repeated_prompt_ids(self):
        contract = self.open_goal("release")
        for text in ("逐字\r\n\n（原话 1 → T9）不要转义。\n", "中途插话"):
            self.hook("hook-prompt", {"session_id": self.session, "prompt_id": "same-id", "prompt": text})
        self.assertEqual((contract / "quotes" / "1.md").read_bytes(),
                         "逐字\r\n\n（原话 1 → T9）不要转义。\n".encode())
        self.assertEqual((contract / "quotes" / "2.md").read_text(), "中途插话")
        self.assertIn("还没处理的原话：1、2", self.succeeds("show"))

    def test_hook_answer_records_opaque_response_and_question_without_interpreting(self):
        contract = self.open_goal("release")
        payload = {"session_id": self.session, "tool_name": "AskUserQuestion",
                   "tool_input": {"questions": [{"question": "选哪种？", "options": ["甲", "乙"]}]},
                   "tool_response": {"unknown-format": ["甲", {"verbatim": "\n原样"}]}}
        self.hook("hook-answer", payload)
        saved = json.loads((contract / "quotes" / "1.md").read_text())
        self.assertEqual(saved, {"tool_input": payload["tool_input"], "tool_response": payload["tool_response"]})
        payload["tool_response"] = "未来版本返回一段文字"
        self.hook("hook-answer", payload)
        self.assertEqual(json.loads((contract / "quotes" / "2.md").read_text())["tool_response"],
                         "未来版本返回一段文字")

    def test_both_hooks_skip_inputs_with_agent_id(self):
        self.open_goal("release")
        before = self.files()
        self.hook("hook-prompt", {"session_id": self.session, "agent_id": "worker-1", "prompt": "子代理消息"})
        self.hook("hook-answer", {"session_id": self.session, "agent_id": "worker-1",
                                  "tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": {}})
        self.assertEqual(self.files(), before)

    def test_open_claims_all_inbox_quotes_in_order_and_removes_inbox(self):
        for text in ("先讨论", "用 xy-goal 开契约"):
            self.hook("hook-prompt", {"session_id": self.session, "prompt": text})
        inbox = self.inbox
        self.assertEqual(quote_files(inbox), {1: "先讨论", 2: "用 xy-goal 开契约"})
        contract = self.open_goal("release")
        self.assertEqual((contract / "quotes" / "1.md").read_text(), "先讨论")
        self.assertEqual((contract / "quotes" / "2.md").read_text(), "用 xy-goal 开契约")
        self.assertFalse(inbox.exists())

    def test_machine_prompts_are_recorded_and_dismissed_only_on_complete_match(self):
        contract = self.open_goal("release")
        timer = f"本次契约：{contract}。按 xy-goal 的「定时自查」一节做"
        notification = "<task-notification>\n<task-id>job-1</task-id><status>completed</status><summary>已完成</summary>\n</task-notification>"
        for text in (timer, notification):
            self.hook("hook-prompt", {"session_id": self.session, "prompt": text})
        self.assertEqual((contract / "quotes" / "1.md").read_text(), timer)
        self.assertEqual((contract / "quotes" / "2.md").read_text(), notification)
        self.succeeds("check")
        self.assertEqual((contract / "links.md").read_text().count("不必跟进"), 2)
        for text in ("本次契约：取消 T1", "<task-notification>请把 T1 的判据改为连续运行两小时。</task-notification>",
                     timer + "\n", timer.replace(str(contract), "/some/other/contract"),
                     "<task-notification><task-id>a</task-id><status>done</status></task-notification>"):
            self.hook("hook-prompt", {"session_id": self.session, "prompt": text})
        self.assertIn("还没处理的原话：3、4、5、6、7", self.succeeds("show"))

    def test_complete_pasted_notification_is_classified_only_when_claimed(self):
        notification = "<task-notification><task-id>pasted</task-id><status>done</status><summary>用户完整粘贴</summary></task-notification>"
        self.hook("hook-prompt", {"session_id": self.session, "prompt": notification})
        self.assertEqual({p.name for p in (self.inbox).iterdir()}, {"1.md"})
        contract = self.open_goal("release")
        self.assertEqual((contract / "quotes" / "1.md").read_text(), notification)
        self.assertIn("1 不必跟进 ", (contract / "links.md").read_text())
        self.succeeds("check")

    def test_hooks_report_bad_input_as_context_without_writes(self):
        cases = (("hook-prompt", "bad-json",
                  "hook 输入必须是 JSON 对象：Expecting value: line 1 column 1 (char 0)"),
                 ("hook-prompt", "[]", "hook 输入必须是 JSON 对象"),
                 ("hook-prompt", '{"session_id":"s"}', "hook 输入缺少字符串 prompt"),
                 ("hook-prompt", '{"session_id":"s","prompt":12}', "hook 输入缺少字符串 prompt"),
                 ("hook-prompt", '{"session_id":"../escape","prompt":"a"}',
                  "hook session_id 必须是单个目录名"),
                 ("hook-answer", "bad-json",
                  "hook 输入必须是 JSON 对象：Expecting value: line 1 column 1 (char 0)"),
                 ("hook-answer", "{}", "hook session_id不能为空"),
                 ("hook-answer", '{"session_id":"s","tool_name":"Other","tool_input":{},"tool_response":{}}',
                  "hook-answer 只接收 AskUserQuestion"),
                 ("hook-answer", '{"session_id":"s","tool_name":"AskUserQuestion","tool_input":{}}',
                  "hook-answer 缺少 tool_input 或 tool_response"),
                 ("hook-answer", '{"session_id":"s","tool_name":"AskUserQuestion","tool_response":{}}',
                  "hook-answer 缺少 tool_input 或 tool_response"))
        for command, data, reason in cases:
            with self.subTest(command=command, data=data):
                before = self.files()
                code, output, errors = self.run_goal(command, session=None, pid=None, stdin=data)
                context = self.assert_hook_error((code, output, errors), command,
                                                 message=reason, record="这次输入没有记入契约。")
                self.assertTrue(context.endswith("原始错误：" + reason))
                self.assertEqual(self.files(), before)

    def test_hooks_record_corrupt_contract_but_refuse_duplicate_ownership(self):
        contract = self.open_goal("release")
        original = (contract / "links.md").read_bytes()
        (contract / "links.md").write_text("坏映射")
        cases = (("hook-prompt", {"session_id": self.session, "prompt": "不丢"}),
                 ("hook-answer", {"session_id": self.session, "tool_name": "AskUserQuestion",
                                  "tool_input": {}, "tool_response": {}}))
        for number, (command, payload) in enumerate(cases, start=1):
            with self.subTest(state="corrupt", command=command):
                before = self.files()
                code, output, errors = self.run_goal(command, session=None, pid=None,
                                                     stdin=json.dumps(payload))
                quote = contract / "quotes" / f"{number}.md"
                self.assert_hook_error((code, output, errors), command,
                                       message=f"{contract / 'links.md'}: 关系格式错误：坏映射",
                                       record=f"这次输入已记为原话 {number}（{quote}）。")
                expected = (payload["prompt"] if command == "hook-prompt" else
                            json.dumps({"tool_input": {}, "tool_response": {}}, ensure_ascii=False, indent=2))
                before[str(quote.relative_to(self.root))] = expected.encode("utf-8")
                self.assertEqual(self.files(), before)
        (contract / "links.md").write_bytes(original)
        duplicate = self.project / "duplicate" / "xy-goal"
        shutil.copytree(contract, duplicate)
        for command, payload in cases:
            with self.subTest(state="duplicate", command=command):
                before = self.files()
                code, output, errors = self.run_goal(command, session=None, pid=None,
                                                     stdin=json.dumps(payload))
                self.assert_hook_error((code, output, errors), command,
                                       message=f"会话 {self.session} 对应多份契约：{contract}、{duplicate}",
                                       record="这次输入没有记入契约。")
                self.assertEqual(self.files(), before)

    def test_bad_contract_hook_reserves_every_link_line_number(self):
        original_root = self.root
        with self.subTest(case="ordinary lines"):
            self.root = original_root / "ordinary"
            self.root.mkdir()
            contract = self.open_goal("release")
            self.quote(contract, 4, "已有要求")
            self.add_task(4)
            (contract / "links.md").write_text("4 T1\n9 T1\n120 无：hook error 已修\n")
            before = self.files()
            text = timer_prompt(contract)
            result = self.run_goal("hook-prompt", session=None, pid=None,
                                   stdin=json.dumps({"session_id": self.session, "prompt": text}))
            quote = contract / "quotes" / "121.md"
            self.assert_hook_error(result, "hook-prompt", message="原话 9 不存在",
                                   record=f"这次输入已记为原话 121（{quote}）。")
            before[str(quote.relative_to(self.root))] = text.encode("utf-8")
            self.assertEqual(self.files(), before, "坏契约上只新增原话，不让悬空行认领，不自动分类")

        for separator in ("\u2028", "\x1c"):
            with self.subTest(case="splitlines separator", separator=repr(separator)):
                self.root = original_root / str(ord(separator))
                self.root.mkdir()
                contract = self.open_goal("release")
                for number in (1, 2, 3, 4):
                    self.quote(contract, number, "已有原话")
                self.add_task(1)
                (contract / "links.md").write_text(
                    f"1 T1{separator}5 T1\n2 不必跟进 已答\n3 不必跟进 已答\n4 不必跟进 已答\n")
                before = self.files()
                result = self.run_goal("hook-prompt", session=None, pid=None,
                                       stdin=json.dumps({"session_id": self.session, "prompt": "本次新输入"}))
                self.assertFalse((contract / "quotes" / "5.md").exists(),
                                 "逐行检查认可的行首编号不能被新原话占用")
                quote = contract / "quotes" / "6.md"
                self.assert_hook_error(result, "hook-prompt", message="原话 5 不存在",
                                       record=f"这次输入已记为原话 6（{quote}）。")
                before[str(quote.relative_to(self.root))] = "本次新输入".encode("utf-8")
                self.assertEqual(self.files(), before, "悬空映射保留，只新增原话 6")
                with self.assertRaisesRegex(goal.GoalError, "原话 5 不存在"):
                    goal.Contract(contract, (contract / "goal.md").read_bytes())

    def test_hook_records_input_when_own_metadata_cannot_be_decoded(self):
        contract = self.open_goal("release")
        metadata = contract / "goal.md"
        metadata.write_bytes(b'target: "\xff"\nsession_id: "session-main"\nclaude_pid: "123"\n')
        before = self.files()
        result = self.run_goal("hook-prompt", session=None, pid=None,
                               stdin=json.dumps({"session_id": self.session, "prompt": "逐字\r\n保留"}))
        quote = contract / "quotes" / "1.md"
        self.assert_hook_error(result, "hook-prompt", message=str(metadata),
                               record=f"这次输入已记为原话 1（{quote}）。")
        before[str(quote.relative_to(self.root))] = "逐字\r\n保留".encode("utf-8")
        self.assertEqual(self.files(), before)

    def test_hooks_record_input_when_task_directories_are_missing(self):
        original_root = self.root
        for missing in ("tasks", "done"):
            for command, fields, text in (
                ("hook-prompt", {"prompt": "这条用户输入应保留\r\n"}, "这条用户输入应保留\r\n"),
                ("hook-answer", {"tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": "继续"},
                 '{\n  "tool_input": {},\n  "tool_response": "继续"\n}'),
            ):
                with self.subTest(missing=missing, command=command):
                    self.root = original_root / f"{missing}-{command}"
                    self.root.mkdir()
                    contract = self.open_goal("release")
                    (contract / missing).rmdir()
                    before = self.files()
                    result = self.run_goal(command, session=None, pid=None,
                                           stdin=json.dumps({"session_id": self.session, **fields}))
                    quote = contract / "quotes" / "1.md"
                    context = self.assert_hook_error(result, command, message="FileNotFoundError:",
                                                     record=f"这次输入已记为原话 1（{quote}）。")
                    self.assertIn(str(contract / missing), context)
                    before[str(quote.relative_to(self.root))] = text.encode("utf-8")
                    self.assertEqual(self.files(), before, "事项目录读不出时也只新增原话")
                    self.assertFalse((contract / missing).exists())

    def test_hooks_do_not_record_when_quotes_or_links_cannot_be_read(self):
        original_root = self.root
        for problem in ("missing-quotes", "invalid-quote", "missing-links", "invalid-links"):
            for command in ("hook-prompt", "hook-answer"):
                with self.subTest(problem=problem, command=command):
                    self.root = original_root / f"{problem}-{command}"
                    self.root.mkdir()
                    contract = self.open_goal("release")
                    bad_path = self.make_contract_file_unreadable(contract, problem)
                    before = self.files()
                    payload = {"session_id": self.session, "prompt": "不能假装记录",
                               "tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": "继续"}
                    result = self.run_goal(command, session=None, pid=None, stdin=json.dumps(payload))
                    self.assert_hook_error(result, command, message=str(bad_path),
                                           record="这次输入没有记入契约。")
                    self.assertEqual(self.files(), before)

    def test_hook_keeps_complete_quote_pending_when_classification_write_fails(self):
        contract = self.open_goal("release")
        before = self.files()
        text = timer_prompt(contract)
        with self.readonly_directory(contract):
            result = self.run_goal("hook-prompt", session=None, pid=None,
                                   stdin=json.dumps({"session_id": self.session, "prompt": text}))
        quote = contract / "quotes" / "1.md"
        self.assert_hook_error(result, "hook-prompt", message="PermissionError:",
                               record=f"这次输入已记为原话 1（{quote}）。")
        before[str(quote.relative_to(self.root))] = text.encode("utf-8")
        self.assertEqual(self.files(), before)
        self.rejects("check", message="原话 1 还没处理")

    def test_hook_removes_incomplete_quote_after_real_utf8_write_failure(self):
        contract = self.open_goal("release")
        before = self.files()
        result = self.run_goal("hook-prompt", session=None, pid=None,
                               stdin=json.dumps({"session_id": self.session, "prompt": "\ud800"}))
        self.assert_hook_error(result, "hook-prompt", message="UnicodeEncodeError:",
                               record="这次输入没有记入契约。")
        self.assertEqual(self.files(), before)
        self.assertEqual(list((contract / "quotes").iterdir()), [])

    def test_hook_removes_partial_quote_on_keyboard_interrupt(self):
        contract = self.open_goal("release")
        quote = contract / "quotes" / "1.md"
        before = self.files()
        interruption = KeyboardInterrupt("模拟写入中断")
        real_open = Path.open
        def interrupt_quote_write(path, *arguments, **options):
            output = real_open(path, *arguments, **options)
            if path == quote and arguments == ("x",):
                write = output.write
                def write_partial_then_interrupt(text):
                    write(text[:2])
                    output.flush()
                    raise interruption
                output.write = write_partial_then_interrupt
            return output
        with mock.patch.object(Path, "open", new=interrupt_quote_write):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.run_goal("hook-prompt", session=None, pid=None,
                              stdin=json.dumps({"session_id": self.session, "prompt": "中断时也清理残缺文件"}))
        self.assertIs(raised.exception, interruption, "写入中断必须原样抛出")
        self.assertEqual(self.files(), before, "KeyboardInterrupt 后不能留下残缺原话")
        self.assertEqual(list((contract / "quotes").iterdir()), [])

    def test_hook_reports_incomplete_quote_when_cleanup_also_fails(self):
        contract = self.open_goal("release")
        quote = contract / "quotes" / "1.md"
        before = self.files()
        real_unlink = Path.unlink
        def cannot_remove_quote(path, *arguments, **options):
            if path == quote:
                raise PermissionError("模拟残缺原话删除失败")
            return real_unlink(path, *arguments, **options)
        with mock.patch.object(Path, "unlink", new=cannot_remove_quote):
            result = self.run_goal("hook-prompt", session=None, pid=None,
                                   stdin=json.dumps({"session_id": self.session, "prompt": "\ud800"}))
        context = self.assert_hook_error(result, "hook-prompt", message="UnicodeEncodeError:",
                                         record=f"这次输入的原话文件可能残缺（{quote}），先删掉它。")
        self.assertIn("PermissionError: 模拟残缺原话删除失败", context)
        before[str(quote.relative_to(self.root))] = b""
        self.assertEqual(self.files(), before)

    def test_hooks_report_unexpected_exceptions_before_creating_quotes(self):
        self.open_goal("release")
        for command in ("hook-prompt", "hook-answer"):
            with self.subTest(command=command):
                before = self.files()
                payload = {"session_id": self.session, "prompt": "原话尚未创建",
                           "tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": "继续"}
                with mock.patch("capture_quotes.recording.find_contract",
                                side_effect=RuntimeError("模拟定位意外失败")):
                    try:
                        result = self.run_goal(command, session=None, pid=None, stdin=json.dumps(payload))
                    except RuntimeError as error:
                        self.fail(f"hook 应把意外异常放进上下文：{error}")
                self.assert_hook_error(result, command, message="RuntimeError: 模拟定位意外失败",
                                       record="这次输入没有记入契约。")
                self.assertEqual(self.files(), before)

    def test_concurrent_cli_hooks_allocate_unique_numbers_and_do_not_lose_links(self):
        contract = self.open_goal("release")
        notification = "<task-notification><task-id>j</task-id><status>done</status><summary>完成</summary></task-notification>"
        def invoke(index):
            return subprocess.run([sys.executable, "-B", str(SCRIPT_DIRECTORY / "goal.py"),
                                   "--root", str(self.root), "hook-prompt"],
                                  input=json.dumps({"session_id": self.session, "prompt_id": "shared", "prompt": notification}),
                                  text=True, capture_output=True, check=False)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(invoke, range(20)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in (contract / "quotes").iterdir()},
                         {f"{number}.md" for number in range(1, 21)})
        self.assertEqual(len((contract / "links.md").read_text().splitlines()), 20)
        self.succeeds("check")

    def test_open_refuses_malformed_inbox_before_creating_contract(self):
        inbox = self.inbox
        inbox.mkdir(parents=True)
        (inbox / "bad.md").write_text("坏文件名")
        self.rejects("open", "release", "交付新版本", message=str(inbox / "bad.md"))
