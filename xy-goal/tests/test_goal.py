"""新契约 API 的行为测试；所有文件只放 TemporaryDirectory。"""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock


SCRIPT_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIRECTORY))
import goal


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session = "session-main"
        self.pid = os.getppid()
        self.now = datetime(2026, 10, 24, 9, 30)

    def run_goal(self, *arguments, session, pid, stdin):
        command_arguments = ["--root", str(self.root), *arguments]
        if arguments and arguments[0] in ("open", "adopt", "migrate") and pid is not None:
            command_arguments.extend(("--claude-pid", str(pid)))
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            old_stdin = sys.stdin
            sys.stdin = StringIO(stdin)
            try:
                code = goal.main(command_arguments, session_id=session, now=self.now)
            finally:
                sys.stdin = old_stdin
        return code, output.getvalue(), errors.getvalue()

    def command(self, *arguments):
        return self.run_goal(*arguments, session=self.session, pid=self.pid, stdin="")

    def succeeds(self, *arguments):
        code, output, errors = self.command(*arguments)
        self.assertEqual(code, 0, errors)
        self.assertEqual(errors, "")
        return output

    def rejects(self, *arguments, message):
        before = self.files()
        code, output, errors = self.command(*arguments)
        self.assertEqual(code, 1, output + errors)
        self.assertIn(message, errors)
        self.assertEqual(self.files(), before, "拒绝前必须完整校验，不可写半截")
        return errors

    def files(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def open_goal(self):
        self.succeeds("open", "release", "交付新版本")
        return self.root / "20261024-093000-release"

    @staticmethod
    def quote(contract, number, text):
        (contract / "quotes" / f"{number}.md").write_text(text, encoding="utf-8")

    def add_task(self, *sources):
        output = self.succeeds("add", "--title", "发布", "--criteria", "测试通过",
                               "--source", *map(str, sources),
                               "--state", "在办", "--pointer", "无")
        self.assertRegex(output, r"T[1-9][0-9]*", "add 必须创建并返回事项编号")
        return output

    def test_open_creates_directory_and_outputs_exact_timer_prompt(self):
        output = self.succeeds("open", "release", "交付新版本")
        contract = self.root / "20261024-093000-release"
        self.assertEqual({p.name for p in contract.iterdir()},
                         {"goal.md", "quotes", "tasks", "done", "links.md"})
        metadata = (contract / "goal.md").read_text(encoding="utf-8")
        self.assertIn("交付新版本", metadata)
        self.assertIn("session-main", metadata)
        self.assertIn(str(self.pid), metadata)
        self.assertEqual(output, f"本次契约：{contract}。按 `~/.claude/skills/xy-goal/SKILL.md`「定时自查」一节做\n")

    def test_show_lists_goal_and_unhandled_quotes_in_numeric_order(self):
        contract = self.open_goal()
        for number in (10, 2, 1):
            self.quote(contract, number, "不要漏掉要求")
        output = self.succeeds("show")
        self.assertIn("交付新版本", output)
        self.assertIn("还没处理的原话：1、2、10", output)

    def test_check_reports_each_unhandled_quote_then_passes_without_them(self):
        contract = self.open_goal()
        self.assertIn("通过", self.succeeds("check"))
        self.quote(contract, 1, "实现功能")
        self.quote(contract, 2, "保留接口")
        errors = self.rejects("check", message="原话 1")
        self.assertIn("原话 2", errors)
        self.assertEqual(len(errors.splitlines()), 2)

    def test_open_refuses_an_existing_contract(self):
        self.open_goal()
        self.rejects("open", "other", "另一个目标", message="已有契约")

    def test_commands_require_exactly_one_contract(self):
        for arguments in (("show",), ("check",), ("close",),
                          ("update", "T1", "--source", "1"),
                          ("dismiss", "1", "--reason", "已答"),
                          ("accept", "T1", "--verdict", "通过"),
                          ("cancel", "T1", "--quote", "1"),
                          ("add", "--title", "实现", "--criteria", "通过",
                           "--source", "1", "--state", "在办", "--pointer", "无")):
            with self.subTest(arguments=arguments):
                self.rejects(*arguments, message="没有契约")

    def test_duplicate_session_contracts_are_rejected(self):
        first = self.open_goal()
        shutil.copytree(first, self.root / "20261024-093001-copy")
        self.rejects("show", message="多份契约")

    def test_check_does_not_validate_another_sessions_tasks(self):
        first = self.open_goal()
        other = self.root / "20261024-093001-other"
        shutil.copytree(first, other)
        metadata = (other / "goal.md").read_text(encoding="utf-8")
        (other / "goal.md").write_text(metadata.replace("session-main", "session-other"),
                                      encoding="utf-8")
        (other / "tasks" / "T1.md").write_text("损坏", encoding="utf-8")
        self.assertIn("通过", self.succeeds("check"))

    def test_foreign_invalid_utf8_metadata_does_not_block_check_or_either_hook(self):
        contract = self.open_goal()
        foreign = self.root / "foreign-contract"
        foreign.mkdir()
        metadata = foreign / "goal.md"
        metadata.write_bytes(b'target: "\xff"\nsession_id: "foreign-session"\nclaude_pid: "123"\n')
        original = metadata.read_bytes()
        self.succeeds("check")
        self.hook("hook-prompt", {"session_id": self.session, "prompt": "本会话新要求"})
        self.hook("hook-answer", {"session_id": self.session, "tool_name": "AskUserQuestion",
                                  "tool_input": {"question": "继续？"}, "tool_response": "继续"})
        self.assertEqual((contract / "quotes" / "1.md").read_text(), "本会话新要求")
        self.assertEqual(json.loads((contract / "quotes" / "2.md").read_text())["tool_response"], "继续")
        self.assertEqual(metadata.read_bytes(), original)

    def test_foreign_unreadable_metadata_does_not_block_own_contract(self):
        contract = self.open_goal()
        foreign = self.root / "foreign-contract"
        foreign.mkdir()
        metadata = foreign / "goal.md"
        metadata.write_text('session_id: "foreign-session"\n')
        metadata.chmod(0)
        try:
            self.succeeds("check")
            self.hook("hook-prompt", {"session_id": self.session, "prompt": "仍能记录"})
            self.assertEqual((contract / "quotes" / "1.md").read_text(), "仍能记录")
        finally:
            metadata.chmod(0o600)

    def test_own_invalid_utf8_metadata_is_still_reported(self):
        contract = self.open_goal()
        metadata = contract / "goal.md"
        metadata.write_bytes(b'target: "\xff"\nsession_id: "session-main"\nclaude_pid: "123"\n')
        self.rejects("check", message=str(metadata))

    def test_add_keeps_many_to_many_sources_only_in_links(self):
        contract = self.open_goal()
        self.quote(contract, 1, "测试后发布")
        self.quote(contract, 2, "使用新流程")
        self.assertIn("T1", self.add_task(1, 2))
        self.assertIn("T2", self.add_task(1))
        self.assertEqual((contract / "links.md").read_text(encoding="utf-8"),
                         "1 T1\n1 T2\n2 T1\n")
        output = self.succeeds("show")
        self.assertIn("T1 发布\n  判据：测试通过\n  出处：原话 1、2\n  状态：在办；指针：无", output)
        self.assertIn("还没处理的原话：无", output)
        self.assertNotIn("source", (contract / "tasks" / "T1.md").read_text())
        self.assertIn("通过", self.succeeds("check"))

    def test_add_refuses_missing_source_and_invalid_state_before_writing(self):
        contract = self.open_goal()
        self.quote(contract, 1, "完成发布")
        base = ("add", "--title", "发布", "--criteria", "通过", "--pointer", "无")
        self.rejects(*base, "--source", "1", "9", "--state", "在办", message="原话 9 不存在")
        for state in ("完成", "等用户：", "等：T1", "在跑：worker"):
            with self.subTest(state=state):
                self.rejects(*base, "--source", "1", "--state", state, message="状态")

    def test_update_each_allowed_field_preserves_quotes_and_changes_show(self):
        contract = self.open_goal()
        self.quote(contract, 1, "发布要求")
        self.quote(contract, 2, "必须两轮通过")
        self.add_task(1)
        before_quotes = {p.name: p.read_bytes() for p in (contract / "quotes").iterdir()}
        for field, value in (("标题", "新发布"), ("状态", "等用户：确认时间"),
                             ("指针", "/tmp/report")):
            self.succeeds("update", "T1", "--field", field, "--value", value)
        self.succeeds("update", "T1", "--field", "判据", "--value", "两轮通过", "--source", "2")
        output = self.succeeds("show")
        for value in ("新发布", "两轮通过", "等用户：确认时间", "/tmp/report", "原话 1、2"):
            self.assertIn(value, output)
        self.assertEqual({p.name: p.read_bytes() for p in (contract / "quotes").iterdir()}, before_quotes)

    def test_update_source_only_adds_relations_without_replacing_old_sources(self):
        contract = self.open_goal()
        for number in (1, 2, 3):
            self.quote(contract, number, "要求")
        self.add_task(1)
        before_task = (contract / "tasks" / "T1.md").read_bytes()
        self.succeeds("update", "T1", "--source", "2", "3", "2")
        self.assertEqual((contract / "tasks" / "T1.md").read_bytes(), before_task)
        self.assertEqual((contract / "links.md").read_text(), "1 T1\n2 T1\n3 T1\n")

    def test_update_refuses_unknown_field_missing_reason_and_missing_task(self):
        contract = self.open_goal()
        self.quote(contract, 1, "要求")
        self.add_task(1)
        cases = (
            (("T1", "--field", "判据", "--value", "变更"), "--source"),
            (("T1", "--field", "出处", "--value", "1"), "字段"),
            (("T1", "--field", "状态", "--value", "等用户："), "状态"),
            (("T1", "--field", "标题"), "--value"),
            (("T1", "--value", "文字"), "--field"),
            (("T1",), "--source"),
            (("T9", "--source", "1"), "T9"),
            (("T1", "--field", "标题", "--value", "发布", "--source", "9"), "原话 9 不存在"),
        )
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                self.rejects("update", *arguments, message=message)

    def test_dismiss_handles_quote_without_touching_original_text(self):
        contract = self.open_goal()
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
        contract = self.open_goal()
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
        contract = self.open_goal()
        self.quote(contract, 1, "发布")
        self.quote(contract, 2, "取消发布")
        self.add_task(1)
        self.rejects("cancel", "T1", "--quote", "9", message="原话 9 不存在")
        self.succeeds("cancel", "T1", "--quote", "2")
        self.assertFalse((contract / "tasks" / "T1.md").exists())
        self.assertIn("2", (contract / "done" / "T1.md").read_text())
        self.assertEqual((contract / "links.md").read_text(), "1 T1\n2 T1\n")
        self.succeeds("check")

    def test_close_refuses_open_tasks_and_unhandled_quotes_then_deletes_only_own_contract(self):
        contract = self.open_goal()
        other = self.root / "untouched.txt"
        other.write_text("别的文件")
        self.quote(contract, 1, "发布")
        self.rejects("close", message="还没处理")
        self.add_task(1)
        self.rejects("close", message="开放事项")
        self.succeeds("accept", "T1", "--verdict", "通过")
        self.succeeds("close")
        self.assertFalse(contract.exists())
        self.assertEqual(other.read_text(), "别的文件")

    def test_all_commands_refuse_corrupt_contract_files_and_name_the_file(self):
        contract = self.open_goal()
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
        contract = self.open_goal()
        self.quote(contract, 1, "完成需求")
        self.succeeds("add", "--title", "长任务", "--criteria", "判" * 5100,
                      "--source", "1", "--state", "在办", "--pointer", "无")
        output = self.succeeds("show")
        self.assertIn("T1", output.splitlines()[-2])
        self.assertIn("300", output.splitlines()[-2])
        self.assertIn("5000", output.splitlines()[-1])
        self.succeeds("check")

    def test_required_arguments_and_empty_fields_are_rejected_without_writes(self):
        self.rejects("open", "release", message="target")
        self.rejects("open", "../escape", "目标", message="slug")
        self.rejects("open", "release", "", message="目标")
        contract = self.open_goal()
        self.quote(contract, 1, "发布")
        for field in ("title", "criteria", "state", "pointer", "source"):
            values = {"title": "发布", "criteria": "通过", "state": "在办", "pointer": "无", "source": "1"}
            arguments = ["add"]
            for name, value in values.items():
                if name != field:
                    arguments.extend((f"--{name}", value))
            with self.subTest(field=field):
                self.rejects(*arguments, message=f"--{field}")
        self.rejects("add", "--title", "", "--criteria", "通过", "--state", "在办",
                     "--pointer", "无", "--source", "1", message="标题")

    def hook(self, command, payload):
        code, output, errors = self.run_goal(command, session=None, pid=None,
                                             stdin=json.dumps(payload, ensure_ascii=False))
        self.assertEqual(code, 0, errors)
        self.assertEqual(output + errors, "", "成功 hook 不向会话注入正文")

    def test_hook_prompt_writes_exact_bytes_and_keeps_repeated_prompt_ids(self):
        contract = self.open_goal()
        for text in ("逐字\r\n\n（原话 1 → T9）不要转义。\n", "中途插话"):
            self.hook("hook-prompt", {"session_id": self.session, "prompt_id": "same-id", "prompt": text})
        self.assertEqual((contract / "quotes" / "1.md").read_bytes(),
                         "逐字\r\n\n（原话 1 → T9）不要转义。\n".encode())
        self.assertEqual((contract / "quotes" / "2.md").read_text(), "中途插话")
        self.assertIn("还没处理的原话：1、2", self.succeeds("show"))

    def test_hook_answer_records_opaque_response_and_question_without_interpreting(self):
        contract = self.open_goal()
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
        self.open_goal()
        before = self.files()
        self.hook("hook-prompt", {"session_id": self.session, "agent_id": "worker-1", "prompt": "子代理消息"})
        self.hook("hook-answer", {"session_id": self.session, "agent_id": "worker-1",
                                  "tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": {}})
        self.assertEqual(self.files(), before)

    def test_open_claims_all_inbox_quotes_in_order_and_removes_inbox(self):
        for text in ("先讨论", "用 xy-goal 开契约"):
            self.hook("hook-prompt", {"session_id": self.session, "prompt": text})
        inbox = self.root / "inbox" / self.session
        self.assertEqual((inbox / "1.md").read_text(), "先讨论")
        self.assertEqual((inbox / "2.md").read_text(), "用 xy-goal 开契约")
        contract = self.open_goal()
        self.assertEqual((contract / "quotes" / "1.md").read_text(), "先讨论")
        self.assertEqual((contract / "quotes" / "2.md").read_text(), "用 xy-goal 开契约")
        self.assertFalse(inbox.exists())

    def test_machine_prompts_are_recorded_and_dismissed_only_on_complete_match(self):
        contract = self.open_goal()
        timer = f"本次契约：{contract}。按 `~/.claude/skills/xy-goal/SKILL.md`「定时自查」一节做"
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
        self.assertEqual({p.name for p in (self.root / "inbox" / self.session).iterdir()}, {"1.md"})
        contract = self.open_goal()
        self.assertEqual((contract / "quotes" / "1.md").read_text(), notification)
        self.assertIn("1 不必跟进 ", (contract / "links.md").read_text())
        self.succeeds("check")

    def test_hooks_return_one_with_reason_and_no_writes_on_bad_input(self):
        cases = (("hook-prompt", "bad-json"),
                 ("hook-prompt", "[]"),
                 ("hook-prompt", '{"session_id":"s"}'),
                 ("hook-prompt", '{"session_id":"s","prompt":12}'),
                 ("hook-prompt", '{"session_id":"../escape","prompt":"a"}'),
                 ("hook-answer", '{"session_id":"s","tool_name":"Other","tool_input":{},"tool_response":{}}'),
                 ("hook-answer", '{"session_id":"s","tool_name":"AskUserQuestion","tool_input":{}}'))
        for command, data in cases:
            with self.subTest(command=command, data=data):
                before = self.files()
                code, output, errors = self.run_goal(command, session=None, pid=None, stdin=data)
                self.assertEqual(code, 1, output + errors)
                self.assertTrue(errors.strip())
                self.assertEqual(self.files(), before)

    def test_hook_refuses_corrupt_contract_and_duplicate_ownership(self):
        contract = self.open_goal()
        original = (contract / "links.md").read_bytes()
        (contract / "links.md").write_text("坏映射")
        before = self.files()
        code, _, errors = self.run_goal("hook-prompt", session=None, pid=None,
                                        stdin=json.dumps({"session_id": self.session, "prompt": "不丢"}))
        self.assertEqual(code, 1)
        self.assertIn("links.md", errors)
        self.assertEqual(self.files(), before)
        (contract / "links.md").write_bytes(original)
        shutil.copytree(contract, self.root / "duplicate")
        code, _, errors = self.run_goal("hook-answer", session=None, pid=None,
                                        stdin=json.dumps({"session_id": self.session, "tool_name": "AskUserQuestion",
                                                          "tool_input": {}, "tool_response": {}}))
        self.assertEqual(code, 1)
        self.assertIn("多份契约", errors)

    def test_concurrent_cli_hooks_allocate_unique_numbers_and_do_not_lose_links(self):
        contract = self.open_goal()
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

    def test_cli_requires_explicit_root_and_reports_missing_session(self):
        for arguments, message in ((["show"], "--root"),
                                   (["--root", str(self.root), "show"], "CLAUDE_CODE_SESSION_ID")):
            environment = os.environ.copy()
            environment.pop("CLAUDE_CODE_SESSION_ID", None)
            result = subprocess.run([sys.executable, "-B", str(SCRIPT_DIRECTORY / "goal.py"), *arguments],
                                    env=environment, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(message, result.stderr)

    def cli_command(self, *arguments):
        environment = os.environ.copy()
        environment["CLAUDE_CODE_SESSION_ID"] = self.session
        environment["CLAUDE_PID"] = str(self.pid)
        return subprocess.run([sys.executable, "-B", str(SCRIPT_DIRECTORY / "goal.py"),
                               "--root", str(self.root), *arguments],
                              env=environment, capture_output=True, text=True, check=False)

    def live_process(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        def stop():
            if child.poll() is None:
                child.terminate()
            child.wait()
        self.addCleanup(stop)
        return child

    def test_open_refuses_live_or_dead_non_ancestor_pid(self):
        child = self.live_process()
        original_root = self.root
        for state in ("live", "dead"):
            with self.subTest(state=state):
                if state == "dead":
                    child.terminate()
                    child.wait()
                self.root = original_root / state
                self.pid = child.pid
                self.rejects("open", "release", "目标", message="祖先")
                self.assertFalse(list(self.root.glob("*/goal.md")))

    def test_migrate_cannot_take_foreign_live_contract_by_copying_its_pid(self):
        child = self.live_process()
        self.pid = child.pid
        old, _ = self.legacy_contract()
        self.session = "unrelated-session"
        before = self.files()
        result = self.cli_command("migrate", str(old), "--claude-pid", str(child.pid))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("祖先", result.stderr)
        self.assertEqual(self.files(), before)
        self.assertIsNone(child.poll())

    def test_adopt_refuses_non_ancestor_pid_before_changing_owner(self):
        contract = self.open_goal()
        old = subprocess.Popen([sys.executable, "-c", "pass"])
        old.wait()
        metadata = contract / "goal.md"
        metadata.write_text(metadata.read_text().replace(f'"{self.pid}"', f'"{old.pid}"'))
        self.session = "unrelated-session"
        self.pid = self.live_process().pid
        self.rejects("adopt", str(contract), message="祖先")

    def test_cli_records_bash_parent_pid(self):
        environment = os.environ.copy()
        environment["CLAUDE_CODE_SESSION_ID"] = self.session
        invocation = shlex.join([sys.executable, "-B", str(SCRIPT_DIRECTORY / "goal.py"),
                                 "--root", str(self.root), "open", "cli", "CLI 目标"])
        invocation += ' --claude-pid "$PPID"'
        wrapper = ("import os, subprocess, sys; print(os.getpid(), flush=True); "
                   f"sys.exit(subprocess.call(['/bin/bash', '-c', {invocation!r}]))")
        result = subprocess.run([sys.executable, "-c", wrapper], env=environment,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        goals = list(self.root.glob("*/goal.md"))
        self.assertEqual(len(goals), 1)
        self.assertIn(f'claude_pid: "{result.stdout.splitlines()[0]}"', goals[0].read_text())

    def test_cli_requires_bash_ppid_for_each_owner_command_even_with_claude_pid_environment(self):
        for arguments in (("open", "release", "目标"),
                          ("adopt", str(self.root / "old-contract")),
                          ("migrate", str(self.root / "old.md"))):
            with self.subTest(arguments=arguments):
                before = self.files()
                result = self.cli_command(*arguments)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                for message in ("--claude-pid", "$PPID", "Claude Code", "Bash"):
                    self.assertIn(message, result.stderr)
                self.assertEqual(self.files(), before)

    def test_cli_rejects_invalid_bash_ppid_with_calling_instructions(self):
        for arguments in (("open", "release", "目标"),
                          ("adopt", str(self.root / "old-contract")),
                          ("migrate", str(self.root / "old.md"))):
            for value in ("", "not-a-pid", "0", "-1"):
                with self.subTest(arguments=arguments, value=value):
                    before = self.files()
                    result = self.cli_command(*arguments, "--claude-pid", value)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    for message in ("--claude-pid", "$PPID", "Claude Code", "Bash"):
                        self.assertIn(message, result.stderr)
                    self.assertEqual(self.files(), before)

    def test_cli_adopt_records_explicit_bash_parent_pid(self):
        contract = self.open_goal()
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        metadata = (contract / "goal.md").read_text()
        (contract / "goal.md").write_text(metadata.replace(f'"{self.pid}"', f'"{child.pid}"'))
        self.session = "new-session"
        result = self.cli_command("adopt", str(contract), "--claude-pid", str(self.pid))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'claude_pid: "{self.pid}"', (contract / "goal.md").read_text())
        self.assertIn("new-session", (contract / "goal.md").read_text())

    def test_cli_migrate_checks_and_records_explicit_bash_parent_pid(self):
        old, _ = self.legacy_contract()
        result = self.cli_command("migrate", str(old), "--claude-pid", str(self.pid))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old.exists())
        self.assertIn(f'claude_pid: "{self.pid}"', (old.with_suffix("") / "goal.md").read_text())

    def test_open_cleans_old_files_by_file_age_and_keeps_recent_children(self):
        old = self.now.timestamp() - 31 * 86400
        fresh = self.now.timestamp() - 5 * 86400
        for tree in ("archive", "inbox"):
            folder = self.root / tree / "old-folder"
            folder.mkdir(parents=True)
            expired, recent, boundary = (folder / name for name in ("expired.md", "recent.md", "boundary.md"))
            for path, modified in ((expired, old), (recent, fresh), (boundary, self.now.timestamp() - 30 * 86400)):
                path.write_text("data")
                os.utime(path, (modified, modified))
            os.utime(folder, (old, old))
        empty = self.root / "archive" / "empty"
        empty.mkdir()
        old_contract = self.root / "20250101-000000-keep"
        old_contract.mkdir()
        (old_contract / "keep.md").write_text("契约不能清理")
        os.utime(old_contract / "keep.md", (old, old))
        self.open_goal()
        for tree in ("archive", "inbox"):
            folder = self.root / tree / "old-folder"
            self.assertFalse((folder / "expired.md").exists())
            self.assertTrue((folder / "recent.md").exists())
            self.assertTrue((folder / "boundary.md").exists())
        self.assertFalse(empty.exists())
        self.assertTrue((old_contract / "keep.md").exists())

    def test_adopt_refuses_live_owner_and_claims_inbox_after_owner_exits(self):
        contract = self.open_goal()
        self.quote(contract, 5, "旧会话要求")
        self.session = "new-session"
        self.rejects("adopt", str(contract), message="原会话进程还在")
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        dead_pid = child.pid
        child.wait()
        metadata = (contract / "goal.md").read_text()
        (contract / "goal.md").write_text(metadata.replace(f'"{self.pid}"', f'"{dead_pid}"'))
        self.hook("hook-prompt", {"session_id": self.session, "prompt": "接管前的话"})
        self.assertEqual(self.succeeds("adopt", str(contract)),
                         f"本次契约：{contract}。按 `~/.claude/skills/xy-goal/SKILL.md`「定时自查」一节做\n")
        self.assertIn("new-session", (contract / "goal.md").read_text())
        self.assertIn(str(self.pid), (contract / "goal.md").read_text())
        self.assertEqual((contract / "quotes" / "6.md").read_text(), "接管前的话")
        self.assertFalse((self.root / "inbox" / self.session).exists())
        self.rejects("adopt", str(contract), message="已有契约")

    def test_open_refuses_malformed_inbox_before_creating_contract(self):
        inbox = self.root / "inbox" / self.session
        inbox.mkdir(parents=True)
        (inbox / "bad.md").write_text("坏文件名")
        self.rejects("open", "release", "交付新版本", message=str(inbox / "bad.md"))

    def legacy_contract(self):
        """按旧清单四行、顶格落点、逐行账本的实际结构造数据，不读取真实契约。"""
        path = self.root / "20261001-081500-legacy.md"
        path.write_text(
            f"# 旧目标\nsession: {self.pid}\ncron_job_id: old-cron\n\n## 清单\n"
            "- [ ] T1 运行工作\n  - 判据：命令通过\n    并检查输出\n  - 出处：原话 1\n"
            "  - 产物：在跑：worker（pid 1234）；下次核：2026-10-01 10:00；/tmp/run.log；保留进度\n"
            "- [ ] T2 等待决定\n  - 判据：用户确定日期\n  - 出处：原话 2\n"
            "  - 产物：等：用户：决定日期；下次核：2026-10-01 10:00；/tmp/user.md\n"
            "- [ ] T3 等待另一项\n  - 判据：任务通过\n  - 出处：原话 1\n"
            "  - 产物：等：T1、T2；下次核：2026-10-01 10:00\n"
            "- [x] T4 待验收\n  - 判据：审查通过\n  - 出处：原话 2\n"
            "  - 产物：待验收：/tmp/result.md；下次核：2026-10-01 10:00\n", encoding="utf-8")
        quotes = ["（原话 1 → T1、T3、T5）第一段\n\n同一条的第二段",
                  "（原话 2 → T2、T4）第二条",
                  "（原话 3 → 无：问题已答）为什么？",
                  "（原话 4 → 规矩：CLAUDE.md）这次的约定",
                  "（原话 5 → T5）已做过的要求",
                  "（原话 6 → 无：已说明）完成另一项",
                  "（原话 7 → T8）取消旧事项"]
        path.with_suffix(".quotes.md").write_text("\n\n".join(quotes) + "\n", encoding="utf-8")
        ledger = ["- T5 已验收（/tmp/report；验收 通过）",
                  "- T6 另一项（原话 6；验收 python3 -m unittest 退出 0）",
                  "- T7 无出处旧记录（/tmp/result；验收 通过）",
                  "- [~] T8 用户取消（原话 7：「不做」）"]
        path.with_suffix(".done.md").write_text("\n".join(ledger) + "\n", encoding="utf-8")
        return path, ledger

    def test_migrate_converts_old_three_files_and_preserves_each_record(self):
        old, ledger = self.legacy_contract()
        self.hook("hook-prompt", {"session_id": self.session, "prompt": "迁移前新消息"})
        output = self.succeeds("migrate", str(old))
        contract = old.with_suffix("")
        self.assertEqual(output, f"本次契约：{contract}。按 `~/.claude/skills/xy-goal/SKILL.md`「定时自查」一节做\n")
        for suffix in (".md", ".quotes.md", ".done.md"):
            self.assertFalse(old.with_suffix(suffix).exists())
        self.assertEqual({p.name for p in (contract / "tasks").iterdir()}, {"T1.md", "T2.md", "T3.md", "T4.md"})
        self.assertEqual({p.name for p in (contract / "done").iterdir()}, {"T5.md", "T6.md", "T7.md", "T8.md"})
        self.assertEqual({p.name for p in (contract / "quotes").iterdir()}, {f"{n}.md" for n in range(1, 9)})
        self.assertEqual((contract / "quotes" / "1.md").read_text(), "第一段\n\n同一条的第二段\n\n")
        self.assertEqual((contract / "quotes" / "7.md").read_text(), "取消旧事项\n")
        self.assertEqual((contract / "quotes" / "8.md").read_text(), "迁移前新消息")
        self.assertFalse((self.root / "inbox" / self.session).exists())
        for index, line in enumerate(ledger, start=5):
            saved = (contract / "done" / f"T{index}.md").read_text()
            self.assertEqual(saved, line + ("\n旧记录无出处\n" if index == 7 else "\n"))
            for fabricated in ("criteria:", "state:", "verdict:"):
                self.assertNotIn(fabricated, saved)
        self.assertEqual((contract / "links.md").read_text(),
                         "1 T1\n1 T3\n1 T5\n2 T2\n2 T4\n3 不必跟进 问题已答\n"
                         "4 不必跟进 旧规矩落点\n5 T5\n6 T6\n6 不必跟进 已说明\n7 T8\n")
        output = self.succeeds("show")
        for text in ("目标：旧目标", "T1 运行工作", "命令通过\\n    并检查输出", "/tmp/run.log；保留进度",
                     "等用户：决定日期", "/tmp/user.md", "T3 等待另一项", "指针：无", "/tmp/result.md"):
            self.assertIn(text, output)
        self.assertEqual(output.count("状态：在办"), 3)
        self.assertNotIn("下次核", output)
        self.succeeds("dismiss", "8", "--reason", "迁移命令已处理")
        self.succeeds("check")
        self.assertIn("T9", self.add_task(1))

    def test_migrate_refuses_another_sessions_legacy_contract_without_changing_it(self):
        old, _ = self.legacy_contract()
        old.write_text(old.read_text().replace(f"session: {self.pid}", f"session: {self.pid + 1}"))
        self.rejects("migrate", str(old), message="不属于本会话")
        self.assertFalse(old.with_suffix("").exists())

    def test_migrate_requires_no_current_contract(self):
        old, _ = self.legacy_contract()
        contract = self.open_goal()
        self.rejects("migrate", str(old), message="已有契约")
        shutil.copytree(contract, self.root / "copy")
        self.rejects("migrate", str(old), message="多份契约")

    def test_migrate_keeps_old_files_when_any_validation_fails(self):
        for problem in ("missing-source", "duplicate-quote", "duplicate-ledger", "dangling-link", "invalid-task", "missing-file"):
            with self.subTest(problem=problem):
                old, ledger = self.legacy_contract()
                quotes_path, ledger_path = old.with_suffix(".quotes.md"), old.with_suffix(".done.md")
                if problem == "missing-source":
                    quotes_path.write_text(quotes_path.read_text().replace("T1、T3、T5", "T3、T5"))
                elif problem == "duplicate-quote":
                    quotes_path.write_text(quotes_path.read_text() + "\n（原话 7 → T8）重复编号")
                elif problem == "duplicate-ledger":
                    ledger_path.write_text(ledger_path.read_text() + ledger[0] + "\n")
                elif problem == "dangling-link":
                    quotes_path.write_text(quotes_path.read_text().replace("T1、T3、T5", "T1、T3、T404"))
                elif problem == "invalid-task":
                    old.write_text(old.read_text().replace("  - 判据：命令通过", "坏判据"))
                else:
                    ledger_path.unlink()
                before = self.files()
                code, output, errors = self.command("migrate", str(old))
                self.assertEqual(code, 1, output + errors)
                self.assertIn(str(old), errors)
                self.assertEqual(self.files(), before)
                self.assertFalse(old.with_suffix("").exists())

    def test_migrate_preserves_indented_annotation_as_quote_body(self):
        old, _ = self.legacy_contract()
        quotes = old.with_suffix(".quotes.md")
        quotes.write_text(quotes.read_text().replace("第一段", "第一段\n （原话 99 → T404）用户贴的片段"))
        self.succeeds("migrate", str(old))
        self.assertIn("\n （原话 99 → T404）用户贴的片段", (old.with_suffix("") / "quotes" / "1.md").read_text())

    def test_migrate_discards_known_legacy_notes_header(self):
        old, _ = self.legacy_contract()
        old.write_text(old.read_text().replace("\n## 清单", "\n笔记：archive/legacy.notes.md\n## 清单"))
        self.succeeds("migrate", str(old))
        contract = old.with_suffix("")
        self.assertEqual({p.name for p in contract.iterdir()},
                         {"goal.md", "quotes", "tasks", "done", "links.md"})
        self.assertEqual(len(list((contract / "tasks").iterdir())), 4)
        self.assertEqual(len(list((contract / "done").iterdir())), 4)
        self.assertEqual(len(list((contract / "quotes").iterdir())), 7)
        self.assertEqual({row.split(": ", 1)[0] for row in (contract / "goal.md").read_text().splitlines()},
                         {"target", "session_id", "claude_pid"})
        self.assertFalse(old.exists())

    def test_migrate_refuses_unknown_landing_and_non_numeric_session(self):
        old, _ = self.legacy_contract()
        quotes = old.with_suffix(".quotes.md")
        original = quotes.read_text()
        quotes.write_text(original.replace("无：问题已答", "未知：内容"))
        self.rejects("migrate", str(old), message="落点")
        quotes.write_text(original)
        old.write_text(old.read_text().replace(f"session: {self.pid}", "session: 会话名"))
        self.rejects("migrate", str(old), message="session")

    def test_failed_mapping_write_rolls_back_task_update(self):
        contract = self.open_goal()
        self.quote(contract, 1, "发布")
        self.quote(contract, 2, "加严判据")
        self.add_task(1)
        real_replace = os.replace
        def cannot_replace_links(source, destination):
            if Path(destination) == contract / "links.md":
                raise PermissionError("模拟映射文件写入失败")
            return real_replace(source, destination)
        with mock.patch("goal.os.replace", side_effect=cannot_replace_links):
            self.rejects("update", "T1", "--field", "判据", "--value", "两轮通过",
                         "--source", "2", message="模拟映射文件写入失败")

    def test_cleanup_does_not_follow_archive_directory_symlink(self):
        outside_cleanup = self.root / "do-not-clean"
        outside_cleanup.mkdir()
        important = outside_cleanup / "keep.md"
        important.write_text("不属于归档")
        old = self.now.timestamp() - 31 * 86400
        os.utime(important, (old, old))
        (self.root / "archive").symlink_to(outside_cleanup, target_is_directory=True)
        self.open_goal()
        self.assertTrue(important.exists(), "不能沿软链清理 archive/inbox 之外的文件")

    def test_inbox_cleanup_expires_quotes_and_keeps_recording(self):
        other_session = "other-session"
        notification = "<task-notification><task-id>j</task-id><status>done</status><summary>完成</summary></task-notification>"
        self.hook("hook-prompt", {"session_id": other_session, "prompt": notification})
        self.hook("hook-prompt", {"session_id": other_session, "prompt": "近期消息"})
        inbox = self.root / "inbox" / other_session
        old = self.now.timestamp() - 31 * 86400
        fresh = self.now.timestamp() - 86400
        os.utime(inbox / "1.md", (old, old))
        os.utime(inbox / "2.md", (fresh, fresh))
        self.open_goal()
        self.assertFalse((inbox / "1.md").exists())
        self.assertEqual((inbox / "2.md").read_text(), "近期消息")
        self.hook("hook-prompt", {"session_id": other_session, "prompt": "继续记录"})
        self.assertEqual((inbox / "3.md").read_text(), "继续记录")

    def test_adopt_refuses_duplicate_contracts_of_the_previous_owner(self):
        contract = self.open_goal()
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        metadata = (contract / "goal.md").read_text()
        (contract / "goal.md").write_text(metadata.replace(f'"{self.pid}"', f'"{child.pid}"'))
        shutil.copytree(contract, self.root / "duplicate-old-owner")
        self.session = "new-session"
        self.rejects("adopt", str(contract), message="多份契约")


if __name__ == "__main__":
    unittest.main()
