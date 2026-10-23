"""新契约 API 的行为测试；所有文件只放 TemporaryDirectory。"""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor


SCRIPT_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIRECTORY))
import goal


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session = "session-main"
        self.pid = os.getpid()
        self.now = datetime(2026, 10, 24, 9, 30)

    def run_goal(self, *arguments, session, pid, stdin):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            old_stdin = sys.stdin
            sys.stdin = StringIO(stdin)
            try:
                code = goal.main(list(arguments), root=self.root,
                                 session_id=session, claude_pid=pid, now=self.now)
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

    def test_complete_pasted_notification_keeps_original_and_inbox_dismissal_survives_open(self):
        notification = "<task-notification><task-id>pasted</task-id><status>done</status><summary>用户完整粘贴</summary></task-notification>"
        self.hook("hook-prompt", {"session_id": self.session, "prompt": notification})
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

    def test_cli_records_claude_ancestor_pid_instead_of_short_lived_shell_pid(self):
        environment = os.environ.copy()
        environment["CLAUDE_CODE_SESSION_ID"] = self.session
        wrapper = ("import os, subprocess, sys; "
                   "print(os.getpid(), flush=True); "
                   f"sys.exit(subprocess.call({[sys.executable, '-B', str(SCRIPT_DIRECTORY / 'goal.py'), '--root', str(self.root), 'open', 'cli', 'CLI 目标']!r}))")
        result = subprocess.run(["claude", "-c", wrapper], executable=sys.executable,
                                env=environment, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        goals = list(self.root.glob("*/goal.md"))
        self.assertEqual(len(goals), 1, "CLI 必须实际创建契约")
        self.assertIn(f'claude_pid: "{result.stdout.splitlines()[0]}"', goals[0].read_text())

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


if __name__ == "__main__":
    unittest.main()
