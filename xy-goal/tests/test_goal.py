"""新契约 API 的行为测试；所有文件只放 TemporaryDirectory。"""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
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
        self.root = Path(self.temporary.name) / "runs"
        self.root.mkdir()
        self.home = Path(self.temporary.name) / "home"
        self.projects = self.home / ".claude" / "projects"
        self.project_name = "-home-xy-goal-tests"
        environment = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        for session in ("session-main", "new-session"):
            self.session_record(session, self.project_name)
        self.session = "session-main"
        self.pid = os.getppid()
        self.now = datetime(2026, 10, 24, 9, 30)

    def session_record(self, session, project_name):
        directory = self.projects / project_name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{session}.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        return path

    @property
    def project(self):
        return self.root / self.project_name

    @property
    def inbox(self):
        return self.project / "xy-goal-inbox" / self.session

    def run_goal(self, *arguments, session, pid, stdin):
        command_arguments = ["--root", str(self.root), *arguments]
        if arguments and arguments[0] in ("open", "adopt") and pid is not None:
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

    def assert_hook_error(self, result, command, *, message, record):
        code, output, errors = result
        self.assertEqual(code, 0, output + errors)
        self.assertEqual(errors, "")
        self.assertEqual(output.count("\n"), 1, "hook 错误只输出一行 JSON")
        payload = json.loads(output)
        self.assertEqual(set(payload), {"hookSpecificOutput"})
        hook = payload["hookSpecificOutput"]
        self.assertEqual(hook["hookEventName"], {
            "hook-prompt": "UserPromptSubmit", "hook-answer": "PostToolUse",
        }[command])
        context = hook["additionalContext"]
        self.assertTrue(context.startswith("请优先修复本会话契约或 hook 问题，再做别的。" + record))
        self.assertIn(message, context.split("原始错误：", 1)[1])
        self.assertNotIn("Traceback", context)
        return context

    def files(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def open_goal(self):
        self.succeeds("open", "release", "交付新版本")
        return self.project / "20261024-093000-release" / "xy-goal"

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
        contract = self.project / "20261024-093000-release" / "xy-goal"
        self.assertTrue((contract / "goal.md").is_file(), "契约必须写入项目下的 xy-goal 子目录")
        self.assertEqual({p.name for p in contract.iterdir()},
                         {"goal.md", "quotes", "tasks", "done", "links.md"})
        metadata = (contract / "goal.md").read_text(encoding="utf-8")
        self.assertIn("交付新版本", metadata)
        self.assertIn("session-main", metadata)
        self.assertIn(str(self.pid), metadata)
        self.assertEqual(output, f"本次契约：{contract}。按 `~/.claude/skills/xy-goal/SKILL.md`「定时自查」一节做\n")

    def replace_project_with_symlink(self):
        outside = Path(self.temporary.name) / f"outside-{self.root.name}"
        if self.project.exists():
            self.project.rename(outside)
        else:
            outside.mkdir()
        (outside / "keep.md").write_bytes(b"outside data must stay unchanged")
        self.project.symlink_to(outside, target_is_directory=True)
        return outside

    @staticmethod
    def directory_entries(directory):
        return {str(path.relative_to(directory)): path.read_bytes() if path.is_file() else None
                for path in directory.rglob("*")}

    def test_open_rejects_symlink_project_without_writing_outside(self):
        outside = self.replace_project_with_symlink()
        before = self.directory_entries(outside)
        self.rejects("open", "release", "交付新版本", message="项目目录")
        self.assertEqual(self.directory_entries(outside), before)
        self.assertTrue(self.project.is_symlink())

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

    def test_existing_contract_writes_reject_symlink_project_without_writing_outside(self):
        original_root = self.root
        for operation in ("add", "update", "dismiss", "accept", "cancel", "close", "adopt",
                          "hook-prompt", "hook-answer"):
            with self.subTest(operation=operation):
                self.root = original_root / operation
                self.root.mkdir()
                contract = self.open_goal()
                self.quote(contract, 1, "发布要求")
                self.add_task(1)
                if operation == "close":
                    self.succeeds("accept", "T1", "--verdict", "通过")
                outside = self.replace_project_with_symlink()
                before = self.directory_entries(outside)
                arguments = {
                    "add": ("add", "--title", "另一项", "--criteria", "通过", "--source", "1",
                            "--state", "在办", "--pointer", "无"),
                    "update": ("update", "T1", "--field", "标题", "--value", "不能越界"),
                    "dismiss": ("dismiss", "1", "--reason", "已答"),
                    "accept": ("accept", "T1", "--verdict", "通过"),
                    "cancel": ("cancel", "T1", "--quote", "1"),
                    "close": ("close",),
                    "adopt": ("adopt", str(contract)),
                    "hook-prompt": ("hook-prompt",),
                    "hook-answer": ("hook-answer",),
                }[operation]
                payload = {"session_id": self.session, "prompt": "不能越界",
                           "tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": "继续"}
                code, output, errors = self.run_goal(
                    *arguments, session=self.session, pid=self.pid, stdin=json.dumps(payload),
                )
                if operation in ("hook-prompt", "hook-answer"):
                    self.assert_hook_error((code, output, errors), operation,
                                           message="项目目录", record="这次输入没有记入契约。")
                else:
                    self.assertEqual(code, 1, output + errors)
                self.assertEqual(self.directory_entries(outside), before)
                self.assertTrue(self.project.is_symlink())

    def test_open_requires_exactly_one_session_record_before_creating_directories(self):
        record = self.projects / self.project_name / f"{self.session}.jsonl"
        record.unlink()
        for matches in (0, 2):
            with self.subTest(matches=matches):
                if matches == 2:
                    self.session_record(self.session, self.project_name)
                    self.session_record(self.session, "-another-project")
                before = list(self.root.rglob("*"))
                self.rejects("open", "release", "交付新版本", message="会话记录")
                self.assertEqual(list(self.root.rglob("*")), before)

    def test_new_session_adopt_requires_exactly_one_record_before_claiming_inbox(self):
        contract = self.open_goal()
        old = subprocess.Popen([sys.executable, "-c", "pass"])
        old.wait()
        metadata = contract / "goal.md"
        metadata.write_text(metadata.read_text().replace(f'"{self.pid}"', f'"{old.pid}"'))
        self.session = "new-session"
        self.hook("hook-prompt", {"session_id": self.session, "prompt": "等待接管"})
        (self.projects / self.project_name / f"{self.session}.jsonl").unlink()
        for matches in (0, 2):
            with self.subTest(matches=matches):
                if matches == 2:
                    self.session_record(self.session, self.project_name)
                    self.session_record(self.session, "-another-project")
                before = list(self.root.rglob("*"))
                self.rejects("adopt", str(contract), message="会话记录")
                self.assertEqual(list(self.root.rglob("*")), before)

    def test_new_session_adopt_claims_inbox_from_its_own_project(self):
        contract = self.open_goal()
        old = subprocess.Popen([sys.executable, "-c", "pass"])
        old.wait()
        metadata = contract / "goal.md"
        metadata.write_text(metadata.read_text().replace(f'"{self.pid}"', f'"{old.pid}"'))
        self.session = "cross-project-session"
        self.session_record(self.session, "-adopting-project")
        self.hook("hook-prompt", {"session_id": self.session, "prompt": "跨项目领取",
                                  "transcript_path": f"/unused/-adopting-project/{self.session}.jsonl"})
        self.succeeds("adopt", str(contract))
        self.assertEqual((contract / "quotes" / "1.md").read_text(), "跨项目领取")
        self.assertFalse((self.root / "-adopting-project" / "xy-goal-inbox" / self.session).exists())
        self.assertTrue((contract / "goal.md").is_file())

    def test_empty_root_is_rejected_without_using_current_directory(self):
        environment = os.environ.copy()
        environment["CLAUDE_CODE_SESSION_ID"] = self.session
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT_DIRECTORY / "goal.py"), "--root", "",
             "open", "release", "交付新版本", "--claude-pid", str(self.pid)],
            cwd=self.root, env=environment, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("--root", result.stderr)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_open_uses_record_parent_name_without_converting_working_directory(self):
        record = self.projects / self.project_name / f"{self.session}.jsonl"
        record.unlink()
        self.session_record(self.session, "-recorded-project-中文")
        output = self.succeeds("open", "release", "交付新版本")
        contract = self.root / "-recorded-project-中文" / "20261024-093000-release" / "xy-goal"
        self.assertTrue((contract / "goal.md").is_file())
        self.assertIn(str(contract), output)
        self.assertFalse(self.project.exists())

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

    def test_existing_contract_commands_and_hooks_do_not_need_session_record(self):
        contract = self.open_goal()
        self.quote(contract, 1, "两个事项")
        self.quote(contract, 2, "问题已答")
        self.add_task(1)
        self.add_task(1)
        shutil.rmtree(self.projects)
        self.succeeds("adopt", str(contract))
        self.succeeds("show")
        self.succeeds("update", "T1", "--field", "标题", "--value", "更新后")
        self.succeeds("dismiss", "2", "--reason", "已答")
        self.succeeds("check")
        code, output, errors = self.run_goal(
            "hook-prompt", session=None, pid=None,
            stdin=json.dumps({"session_id": self.session, "prompt": "取消第二项"}),
        )
        self.assertEqual((code, output, errors), (0, "", ""))
        self.succeeds("accept", "T1", "--verdict", "通过")
        self.succeeds("cancel", "T2", "--quote", "3")
        self.succeeds("close")
        self.assertFalse((contract / "goal.md").exists())

    def test_trash_contract_is_neither_found_by_hook_nor_adopted(self):
        contract = self.open_goal()
        trash = self.root / ".runs-cleaner-trash" / "retired-item"
        trash.parent.mkdir()
        contract.parent.rename(trash)
        before = {path.relative_to(trash): path.read_bytes()
                  for path in trash.rglob("*") if path.is_file()}
        self.hook("hook-prompt", {"session_id": self.session, "prompt": "回到 inbox"})
        self.assertEqual((self.inbox / "1.md").read_text(), "回到 inbox")
        self.rejects("adopt", str(trash / "xy-goal"), message="契约目录")
        self.assertEqual({path.relative_to(trash): path.read_bytes()
                          for path in trash.rglob("*") if path.is_file()}, before)

    def test_adopt_rejects_paths_outside_contract_layer(self):
        contract = self.open_goal()
        self.session = "new-session"
        for destination in (
            self.root / "ordinary" / "item" / "xy-goal",
            self.root / "-project" / "xy-goal",
            self.root / "-project" / "item" / "nested" / "xy-goal",
            self.root / "-project" / "item" / "other-tool",
        ):
            with self.subTest(destination=destination):
                shutil.copytree(contract, destination)
                self.rejects("adopt", str(destination), message="契约目录")

    def test_contract_shares_item_with_runner_and_agent_files(self):
        contract = self.open_goal()
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
        self.succeeds("update", "T1", "--field", "指针", "--value", str(contract.parent / "report.md"))
        self.succeeds("accept", "T1", "--verdict", "通过")
        before = self.files()
        self.succeeds("close")
        del before[str((contract / "goal.md").relative_to(self.root))]
        self.assertEqual(self.files(), before)

    def test_item_names_archive_and_inbox_have_no_special_meaning(self):
        contract = self.open_goal()
        for name in ("archive", "inbox"):
            destination = self.project / name
            contract.parent.rename(destination)
            contract = destination / "xy-goal"
            self.succeeds("show")
            self.hook("hook-prompt", {"session_id": self.session, "prompt": name})
            self.assertTrue((contract / "goal.md").is_file())
        self.assertEqual((contract / "quotes" / "1.md").read_text(), "archive")
        self.assertEqual((contract / "quotes" / "2.md").read_text(), "inbox")

    def test_close_returns_hooks_to_inbox_and_allows_a_new_contract(self):
        contract = self.open_goal()
        self.quote(contract, 1, "第一件事")
        self.succeeds("dismiss", "1", "--reason", "已答")
        self.succeeds("close")
        before = {path.relative_to(contract): path.read_bytes()
                  for path in contract.rglob("*") if path.is_file()}
        self.hook("hook-prompt", {"session_id": self.session, "prompt": "下一件事"})
        self.assertEqual((self.inbox / "1.md").read_text(), "下一件事")
        self.succeeds("open", "next", "下一件事")
        next_contract = self.project / "20261024-093000-next" / "xy-goal"
        self.assertEqual((next_contract / "quotes" / "1.md").read_text(), "下一件事")
        self.assertFalse(self.inbox.exists())
        self.assertEqual({path.relative_to(contract): path.read_bytes()
                          for path in contract.rglob("*") if path.is_file()}, before)

    def test_all_commands_refuse_missing_or_non_directory_root_without_writes(self):
        original_root = self.root
        commands = (
            (("hook-prompt",), {"session_id": self.session, "prompt": "不能丢失的原话"}),
            (("hook-answer",), {"session_id": self.session, "tool_name": "AskUserQuestion",
                                "tool_input": {"question": "继续？"}, "tool_response": "继续"}),
            (("open", "release", "交付新版本", "--claude-pid", str(self.pid)), None),
            (("adopt", str(original_root / "old-contract"), "--claude-pid", str(self.pid)), None),
            (("show",), None),
            (("check",), None),
            (("close",), None),
            (("add", "--title", "发布", "--criteria", "通过", "--source", "1",
              "--state", "在办", "--pointer", "无"), None),
            (("update", "T1", "--source", "1"), None),
            (("dismiss", "1", "--reason", "已答"), None),
            (("accept", "T1", "--verdict", "通过"), None),
            (("cancel", "T1", "--quote", "1"), None),
        )
        for state in ("missing", "file"):
            for arguments, payload in commands:
                with self.subTest(state=state, command=arguments[0]):
                    directory = original_root / f"{state}-{arguments[0]}"
                    directory.mkdir()
                    self.root = directory / "parent" / "root"
                    if state == "file":
                        self.root.parent.mkdir()
                        self.root.write_bytes(b"keep root file unchanged")
                    code, output, errors = self.run_goal(
                        *arguments, session=self.session, pid=None, stdin=json.dumps(payload),
                    )
                    if state == "missing":
                        self.assertEqual(list(directory.iterdir()), [], "失败不能创建根目录或父目录")
                    else:
                        self.assertEqual(self.root.read_bytes(), b"keep root file unchanged")
                        self.assertEqual(list(self.root.parent.iterdir()), [self.root])
                    if arguments[0] in ("hook-prompt", "hook-answer"):
                        self.assert_hook_error((code, output, errors), arguments[0],
                                               message=str(self.root), record="这次输入没有记入契约。")
                    else:
                        self.assertEqual(code, 1, output + errors)
                        self.assertEqual(output, "")
                        self.assertIn(str(self.root), errors)

    def wait_until_root_is_open(self, process, descriptor):
        deadline = time.monotonic() + 10
        while process.poll() is None and time.monotonic() < deadline:
            for path in (Path("/proc") / str(process.pid) / "fd").iterdir():
                try:
                    if os.path.samefile(path, descriptor):
                        return
                except FileNotFoundError:
                    continue  # 子进程可能刚关闭导入模块时打开的文件。
            time.sleep(0.01)
        self.fail("子进程未在超时前打开已锁住的根目录")

    def test_waiting_hook_and_open_refuse_deleted_or_replaced_root_without_writes(self):
        original_root = self.root
        environment = os.environ.copy()
        environment["CLAUDE_CODE_SESSION_ID"] = self.session
        for operation in ("hook-prompt", "open"):
            for root_type, change in (("directory", "deleted"), ("directory", "replaced"),
                                      ("symlink", "deleted"), ("symlink", "replaced")):
                with self.subTest(operation=operation, root_type=root_type, change=change):
                    directory = original_root / f"{operation}-{root_type}-{change}"
                    directory.mkdir()
                    self.root = directory / "root"
                    target = directory / "original" if root_type == "symlink" else self.root
                    target.mkdir()
                    (target / "keep.md").write_bytes(b"original root")
                    replacement = directory / "replacement"
                    replacement.mkdir()
                    (replacement / "keep.md").write_bytes(b"replacement root")
                    if root_type == "symlink":
                        self.root.symlink_to(target, target_is_directory=True)
                    arguments = (("hook-prompt",) if operation == "hook-prompt" else
                                 ("open", "release", "交付新版本", "--claude-pid", str(self.pid)))
                    descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX)
                        with tempfile.TemporaryFile(mode="w+", dir=original_root) as incoming:
                            json.dump({"session_id": self.session, "prompt": "排在锁后的原话"}, incoming)
                            incoming.seek(0)
                            with subprocess.Popen(
                                [sys.executable, "-B", str(SCRIPT_DIRECTORY / "goal.py"),
                                 "--root", str(self.root), *arguments],
                                stdin=incoming, env=environment, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            ) as process:
                                try:
                                    # 观察真实 CLI 的目录 fd；父进程持锁，子进程还不能执行命令。
                                    self.wait_until_root_is_open(process, descriptor)
                                    if root_type == "symlink":
                                        self.root.unlink()
                                        if change == "replaced":
                                            self.root.symlink_to(replacement, target_is_directory=True)
                                    elif change == "deleted":
                                        shutil.rmtree(self.root)
                                    else:
                                        self.root.rename(directory / "retired")
                                        replacement.rename(self.root)
                                    before = {str(path.relative_to(directory)):
                                              path.read_bytes() if path.is_file() else None
                                              for path in directory.rglob("*")}
                                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                                    output, errors = process.communicate(timeout=10)
                                    self.assertEqual(
                                        {str(path.relative_to(directory)):
                                         path.read_bytes() if path.is_file() else None
                                         for path in directory.rglob("*")}, before,
                                        "根目录失效后不能创建目录或写入文件",
                                    )
                                    if operation == "hook-prompt":
                                        self.assert_hook_error((process.returncode, output, errors), operation,
                                                               message=str(self.root),
                                                               record="这次输入没有记入契约。")
                                    else:
                                        self.assertEqual(process.returncode, 1, output + errors)
                                        self.assertEqual(output, "")
                                        self.assertIn(str(self.root), errors)
                                finally:
                                    if process.poll() is None:
                                        process.kill()
                    finally:
                        os.close(descriptor)

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
        shutil.copytree(first, self.project / "20261024-093001-copy" / "xy-goal")
        self.rejects("show", message="多份契约")

    def test_check_does_not_validate_another_sessions_tasks(self):
        first = self.open_goal()
        other = self.project / "20261024-093001-other" / "xy-goal"
        shutil.copytree(first, other)
        metadata = (other / "goal.md").read_text(encoding="utf-8")
        (other / "goal.md").write_text(metadata.replace("session-main", "session-other"),
                                      encoding="utf-8")
        (other / "tasks" / "T1.md").write_text("损坏", encoding="utf-8")
        self.assertIn("通过", self.succeeds("check"))

    def test_foreign_invalid_utf8_metadata_does_not_block_check_or_either_hook(self):
        contract = self.open_goal()
        foreign = self.project / "foreign-contract" / "xy-goal"
        foreign.mkdir(parents=True)
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
        foreign = self.project / "foreign-contract" / "xy-goal"
        foreign.mkdir(parents=True)
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

    def test_relink_repairs_an_open_tasks_only_dangling_source(self):
        contract = self.open_goal()
        self.quote(contract, 1, "真实出处")
        self.add_task(1)
        (contract / "links.md").write_text("9 T1\n")
        before = self.files()
        output = self.succeeds("relink", "T1", "--source", "1")
        self.assertIn("9 T1", output)
        self.assertIn("原话 9 不存在", output)
        self.assertTrue(output.endswith(f"修复完成：{contract}\n"))
        before[str((contract / "links.md").relative_to(self.root))] = b"1 T1\n"
        self.assertEqual(self.files(), before, "relink 只写 links.md")
        self.succeeds("check")

    def test_relink_commits_each_repair_before_remaining_contract_validation(self):
        contract = self.open_goal()
        self.quote(contract, 1, "第一项")
        self.quote(contract, 2, "第二项")
        self.add_task(1)
        self.add_task(2)
        (contract / "links.md").write_text("9 T1\n10 T2\n")
        before = self.files()
        code, output, errors = self.command("relink", "T1", "--source", "1")
        self.assertEqual(code, 1, output + errors)
        self.assertIn("9 T1", output)
        self.assertIn("10 T2", output)
        self.assertIn(f"{contract / 'tasks' / 'T2.md'}: 开放事项没有出处", errors)
        self.assertNotIn("修复完成", output)
        before[str((contract / "links.md").relative_to(self.root))] = b"1 T1\n"
        self.assertEqual(self.files(), before, "返回 1 仍保留本次修复，才能逐项收敛")
        self.assertIn("修复完成", self.succeeds("relink", "T2", "--source", "2"))
        self.assertEqual((contract / "links.md").read_text(), "1 T1\n2 T2\n")
        self.succeeds("check")

    def test_relink_keeps_valid_links_and_dismissals_and_prints_every_bad_line(self):
        contract = self.open_goal()
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
        contract = self.open_goal()
        self.quote(contract, 1, "正常要求")
        self.add_task(1)
        self.rejects("relink", message="契约完好，挂出处用 update")
        self.rejects("relink", "T1", "--source", "1", message="契约完好，挂出处用 update")

    def test_relink_requires_task_and_source_together_before_writing(self):
        contract = self.open_goal()
        (contract / "links.md").write_text("坏映射\n")
        for arguments in (("T1",), ("--source", "1")):
            with self.subTest(arguments=arguments):
                self.rejects("relink", *arguments, message="同时")
        self.rejects("relink", "T1", "--source", "不是数字", message="invalid int value")

    def test_relink_rejects_closed_or_missing_tasks_and_missing_quotes_without_writes(self):
        contract = self.open_goal()
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

    def test_relink_refuses_unreadable_links_or_quotes_without_writes(self):
        original_root = self.root
        for problem in ("missing-links", "invalid-links", "missing-quotes", "invalid-quote"):
            with self.subTest(problem=problem):
                self.root = original_root / problem
                self.root.mkdir()
                contract = self.open_goal()
                if problem == "missing-quotes":
                    bad_path = contract / "quotes"
                    bad_path.rmdir()
                elif problem == "invalid-quote":
                    bad_path = contract / "quotes" / "1.md"
                    bad_path.write_bytes(b'\xff')
                else:
                    bad_path = contract / "links.md"
                    if problem == "missing-links":
                        bad_path.unlink()
                    else:
                        bad_path.write_bytes(b'\xff')
                self.rejects("relink", message=str(bad_path))

    def test_hook_with_empty_quotes_can_be_repaired_without_claiming_dangling_number(self):
        contract = self.open_goal()
        (contract / "tasks" / "T1.md").write_text(
            'title: "手写事项"\ncriteria: "完成要求"\nstate: "在办"\npointer: "无"\n')
        (contract / "links.md").write_text("1 T1\n")
        before = self.files()
        result = self.run_goal("hook-prompt", session=None, pid=None,
                               stdin=json.dumps({"session_id": self.session, "prompt": "本次输入"}))
        quote = contract / "quotes" / "2.md"
        self.assert_hook_error(result, "hook-prompt", message="原话 1 不存在",
                               record=f"这次输入已记为原话 2（{quote}）。")
        before[str(quote.relative_to(self.root))] = "本次输入".encode("utf-8")
        self.assertEqual(self.files(), before)
        self.succeeds("relink", "T1", "--source", "2")
        self.assertFalse((contract / "quotes" / "1.md").exists())
        self.assertEqual((contract / "links.md").read_text(), "2 T1\n")
        self.succeeds("check")

    def test_link_validation_errors_include_repair_hint(self):
        contract = self.open_goal()
        self.quote(contract, 1, "原要求")
        self.add_task(1)
        hint = "；用 relink [<事项> --source <编号>…] 修复，编号看 quotes/\n"
        cases = (("坏映射\n", "关系格式错误"),
                 ("9 T1\n", "原话 9 不存在"),
                 ("1 T9\n", "事项 T9 不存在"),
                 ("1 T1\n1 T1\n", "关系重复"),
                 ("1 T1\n1 不必跟进    \n", "不必跟进原因为空或重复"),
                 ("1 T1\n1 不必跟进 已答\n1 不必跟进 重复\n", "不必跟进原因为空或重复"),
                 ("", "开放事项没有出处"))
        for links, message in cases:
            with self.subTest(message=message, links=links):
                (contract / "links.md").write_text(links)
                errors = self.rejects("check", message=message)
                self.assertTrue(errors.endswith(hint), errors)

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

    def test_closing_task_without_final_lf_preserves_fields_and_keeps_hook_working(self):
        original_root = self.root
        for operation in ("accept", "cancel"):
            with self.subTest(operation=operation):
                self.root = original_root / operation
                self.root.mkdir()
                contract = self.open_goal()
                self.quote(contract, 1, "测试后发布，或取消发布")
                self.add_task(1)
                task = contract / "tasks" / "T1.md"
                task.write_bytes(task.read_bytes().removesuffix(b"\n"))
                self.succeeds("check")
                if operation == "accept":
                    self.succeeds("accept", "T1", "--verdict", "验证通过")
                    conclusion = {"verdict": "验证通过"}
                else:
                    self.succeeds("cancel", "T1", "--quote", "1")
                    conclusion = {"cancel_quote": "1"}
                self.succeeds("check")
                self.assertEqual(goal.Contract(contract).done["T1"], {
                    "title": "发布", "criteria": "测试通过", "state": "在办", "pointer": "无", **conclusion,
                })
                self.assertFalse(task.exists())
                self.hook("hook-prompt", {"session_id": self.session, "prompt": "后续要求"})
                self.assertEqual((contract / "quotes" / "2.md").read_text(), "后续要求")
                self.assertIn("还没处理的原话：2", self.succeeds("show"))

    def test_close_refuses_open_tasks_and_unhandled_quotes_then_removes_only_goal_file(self):
        contract = self.open_goal()
        other = self.root / "untouched.txt"
        other.write_text("别的文件")
        self.quote(contract, 1, "发布")
        self.rejects("close", message="还没处理")
        self.add_task(1)
        self.rejects("close", message="开放事项")
        self.succeeds("accept", "T1", "--verdict", "通过")
        before = self.files()
        self.succeeds("close")
        del before[str((contract / "goal.md").relative_to(self.root))]
        self.assertEqual(self.files(), before)
        self.assertTrue(contract.is_dir())
        self.assertEqual(other.read_text(), "别的文件")

    def contract_with_legacy_done(self):
        """直接写已迁入 done/ 的账本结构，不读取真实契约或调用旧格式入口。"""
        contract = self.open_goal()
        self.quote(contract, 1, "交付实验报告")
        self.quote(contract, 396, "这个 early stop 就作为我们现在的标准流程了")
        records = {
            "T1": "- T1 实验报告（docs/experiments/report.md；验收 终审通过，verify.sh rc=0）\n",
            "T125": "- [~] T125 两次搜参续跑（原话 396：「这个 early stop 就作为我们现在的标准流程了」）\n",
            "T500": "- T500 批准清单匹配（spec.md 第三稿；验收 主审第二轮通过）\n旧记录无出处\n",
        }
        for task, text in records.items():
            (contract / "done" / f"{task}.md").write_text(text, encoding="utf-8")
        (contract / "links.md").write_text("1 T1\n396 T125\n", encoding="utf-8")
        return contract

    def test_show_reads_legacy_done_without_listing_closed_tasks(self):
        self.contract_with_legacy_done()
        before = self.files()
        self.assertEqual(self.succeeds("show"), "目标：交付新版本\n还没处理的原话：无\n")
        self.assertEqual(self.files(), before)

    def test_check_reads_legacy_done_without_changing_records(self):
        contract = self.contract_with_legacy_done()
        before = self.files()
        self.assertEqual(self.succeeds("check"), f"通过：{contract}\n")
        self.assertEqual(self.files(), before)

    def test_close_preserves_legacy_done(self):
        contract = self.contract_with_legacy_done()
        before = self.files()
        self.succeeds("close")
        del before[str((contract / "goal.md").relative_to(self.root))]
        self.assertEqual(self.files(), before)
        self.assertTrue(contract.is_dir())

    def test_add_does_not_reuse_legacy_done_numbers(self):
        contract = self.contract_with_legacy_done()
        self.quote(contract, 397, "新增发布要求")
        self.assertEqual(self.add_task(397), "T501\n")
        self.assertTrue((contract / "tasks" / "T501.md").exists())
        self.succeeds("check")

    def test_check_rejects_legacy_done_with_inconsistent_sources(self):
        original_root = self.root
        for problem, links, message in (
                ("missing-body-source", "1 T1\n1 T125\n396 不必跟进 已答\n", "账本正文原话缺少映射"),
                ("missing-source-marker", "396 T125\n1 不必跟进 已答\n", "旧记录无出处标记与映射不符"),
                ("mapped-without-source", "1 T1\n1 T500\n396 T125\n", "旧记录无出处标记与映射不符")):
            with self.subTest(problem=problem):
                self.root = original_root / problem
                self.root.mkdir()
                contract = self.contract_with_legacy_done()
                (contract / "links.md").write_text(links, encoding="utf-8")
                self.rejects("check", message=message)

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

    def test_unicode_line_separators_round_trip_in_native_fields(self):
        original_root = self.root
        for separator in ("\u2028", "\u2029", "\u0085"):
            for operation in ("add", "update", "accept"):
                with self.subTest(separator=repr(separator), operation=operation):
                    self.root = original_root / f"{ord(separator)}-{operation}"
                    self.root.mkdir()
                    contract = self.open_goal()
                    self.quote(contract, 1, "按要求完成")
                    text = "甲" + separator + "乙"
                    if operation == "add":
                        self.succeeds("add", "--title", text, "--criteria", "通过", "--source", "1",
                                      "--state", "在办", "--pointer", "无")
                    else:
                        self.add_task(1)
                        if operation == "update":
                            self.succeeds("update", "T1", "--field", "指针", "--value", text)
                        else:
                            self.succeeds("accept", "T1", "--verdict", text)
                    self.succeeds("check")
                    saved = goal.Contract(contract)
                    if operation == "accept":
                        self.assertEqual(saved.done["T1"]["verdict"], text)
                    else:
                        self.assertEqual(saved.tasks["T1"]["title" if operation == "add" else "pointer"], text)
                        self.assertEqual(len(self.succeeds("show").splitlines()), 6)

    def test_dismiss_rejects_all_record_line_breaks_before_writing(self):
        original_root = self.root
        for separator in ("\u2028", "\u2029", "\u0085", "\v", "\f", "\x1c", "\x1d", "\x1e"):
            with self.subTest(separator=repr(separator)):
                self.root = original_root / str(ord(separator))
                self.root.mkdir()
                contract = self.open_goal()
                self.quote(contract, 1, "问题已回答")
                self.rejects("dismiss", "1", "--reason", "甲" + separator + "乙", message="一行")

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
        payload = {"transcript_path": str(self.projects / self.project_name / f"{payload['session_id']}.jsonl"),
                   **payload}
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
        inbox = self.inbox
        self.assertEqual(goal.quote_files(inbox), {1: "先讨论", 2: "用 xy-goal 开契约"})
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
        self.assertEqual({p.name for p in (self.inbox).iterdir()}, {"1.md"})
        contract = self.open_goal()
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
        contract = self.open_goal()
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

    def test_hook_error_preserves_original_line_breaks(self):
        contract = self.open_goal()
        destination = self.project / "task\nwith-line-break"
        contract.parent.rename(destination)
        contract = destination / "xy-goal"
        (contract / "links.md").write_text("坏映射")
        before = self.files()
        code, output, errors = self.run_goal("hook-prompt", session=None, pid=None,
                                            stdin=json.dumps({"session_id": self.session, "prompt": "不丢"}))
        quote = contract / "quotes" / "1.md"
        self.assert_hook_error((code, output, errors), "hook-prompt",
                               message=f"{contract / 'links.md'}: 关系格式错误：坏映射",
                               record=f"这次输入已记为原话 1（{quote}）。")
        before[str(quote.relative_to(self.root))] = "不丢".encode("utf-8")
        self.assertEqual(self.files(), before)

    def test_bad_contract_hook_skips_every_number_at_the_start_of_links_lines(self):
        contract = self.open_goal()
        self.quote(contract, 4, "已有要求")
        self.add_task(4)
        (contract / "links.md").write_text("4 T1\n9 T1\n120 无：hook error 已修\n")
        before = self.files()
        text = goal.timer_prompt(contract)
        result = self.run_goal("hook-prompt", session=None, pid=None,
                               stdin=json.dumps({"session_id": self.session, "prompt": text}))
        quote = contract / "quotes" / "121.md"
        self.assert_hook_error(result, "hook-prompt", message="原话 9 不存在",
                               record=f"这次输入已记为原话 121（{quote}）。")
        before[str(quote.relative_to(self.root))] = text.encode("utf-8")
        self.assertEqual(self.files(), before, "坏契约上只新增原话，不让悬空行认领，不自动分类")

    def test_bad_contract_hook_reserves_numbers_after_splitlines_separators(self):
        original_root = self.root
        for separator in ("\u2028", "\x1c"):
            with self.subTest(separator=repr(separator)):
                self.root = original_root / str(ord(separator))
                self.root.mkdir()
                contract = self.open_goal()
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
                    goal.Contract(contract)

    def test_hook_records_input_when_own_metadata_cannot_be_decoded(self):
        contract = self.open_goal()
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

    def test_hooks_do_not_record_when_quotes_or_links_cannot_be_read(self):
        original_root = self.root
        for problem in ("missing-quotes", "invalid-quote", "missing-links", "invalid-links"):
            for command in ("hook-prompt", "hook-answer"):
                with self.subTest(problem=problem, command=command):
                    self.root = original_root / f"{problem}-{command}"
                    self.root.mkdir()
                    contract = self.open_goal()
                    if problem == "missing-quotes":
                        (contract / "quotes").rmdir()
                        bad_path = contract / "quotes"
                    elif problem == "invalid-quote":
                        bad_path = contract / "quotes" / "1.md"
                        bad_path.write_bytes(b'\xff')
                    else:
                        bad_path = contract / "links.md"
                        if problem == "missing-links":
                            bad_path.unlink()
                        else:
                            bad_path.write_bytes(b'\xff')
                    before = self.files()
                    payload = {"session_id": self.session, "prompt": "不能假装记录",
                               "tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": "继续"}
                    result = self.run_goal(command, session=None, pid=None, stdin=json.dumps(payload))
                    self.assert_hook_error(result, command, message=str(bad_path),
                                           record="这次输入没有记入契约。")
                    self.assertEqual(self.files(), before)

    def test_hook_keeps_complete_quote_pending_when_classification_write_fails(self):
        contract = self.open_goal()
        before = self.files()
        text = goal.timer_prompt(contract)
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
        contract = self.open_goal()
        before = self.files()
        result = self.run_goal("hook-prompt", session=None, pid=None,
                               stdin=json.dumps({"session_id": self.session, "prompt": "\ud800"}))
        self.assert_hook_error(result, "hook-prompt", message="UnicodeEncodeError:",
                               record="这次输入没有记入契约。")
        self.assertEqual(self.files(), before)
        self.assertEqual(list((contract / "quotes").iterdir()), [])

    def test_hook_removes_partial_quote_on_keyboard_interrupt(self):
        contract = self.open_goal()
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
        contract = self.open_goal()
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
        self.open_goal()
        for command in ("hook-prompt", "hook-answer"):
            with self.subTest(command=command):
                before = self.files()
                payload = {"session_id": self.session, "prompt": "原话尚未创建",
                           "tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": "继续"}
                with mock.patch("goal.find_contract", side_effect=RuntimeError("模拟定位意外失败")):
                    try:
                        result = self.run_goal(command, session=None, pid=None, stdin=json.dumps(payload))
                    except RuntimeError as error:
                        self.fail(f"hook 应把意外异常放进上下文：{error}")
                self.assert_hook_error(result, command, message="RuntimeError: 模拟定位意外失败",
                                       record="这次输入没有记入契约。")
                self.assertEqual(self.files(), before)

    def test_non_hook_commands_still_raise_unexpected_exceptions(self):
        before = self.files()
        with mock.patch("goal.find_contract", side_effect=RuntimeError("模拟定位意外失败")):
            with self.assertRaisesRegex(RuntimeError, "模拟定位意外失败"):
                self.command("show")
        self.assertEqual(self.files(), before)

    def test_parse_errors_hint_hooks_without_treating_root_values_as_commands(self):
        missing_fields = "the following arguments are required: --title, --criteria, --state, --pointer, --source\n"
        cases = [(["show"], None, "the following arguments are required: --root\n")]
        for command in ("hook-prompt", "hook-answer"):
            cases.extend((
                ([command], command, "the following arguments are required: --root"),
                (["--root", str(self.root), command, "--unexpected"], command,
                 "unrecognized arguments: --unexpected"),
                (["--root", "--bad", command], command, "argument --root: expected one argument"),
                (["--root", "--root", command, "show"], None, "argument --root: expected one argument\n"),
                (["--root", command, "add"], None, missing_fields),
                (["--roo", command, "add"], None, missing_fields),
                ([f"--root={command}", "add"], None, missing_fields),
                (["--root", command], None, "the following arguments are required: command\n"),
                ([f"--root={command}"], None, "the following arguments are required: command\n"),
            ))
        for arguments, command, expected in cases:
            with self.subTest(arguments=arguments):
                before = self.files()
                output, errors = StringIO(), StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    code = goal.main(arguments, session_id=self.session, now=self.now)
                result = code, output.getvalue(), errors.getvalue()
                if command is None:
                    self.assertEqual(result, (1, "", expected))
                else:
                    context = self.assert_hook_error(result, command, message=expected,
                                                     record="这次输入没有记入契约。")
                    self.assertTrue(context.endswith("原始错误：" + expected))
                self.assertEqual(self.files(), before)

    def test_cli_errors_preserve_exact_output(self):
        # argparse 的候选列表文案随 Python 版本变化，比较当前解释器的原始错误。
        with self.assertRaises(goal.GoalError) as parse_error:
            goal.parser().parse_args(["--root", str(self.root), "unknown"])
        cases = ((("show",), "本会话没有契约\n"),
                 (("add",), "the following arguments are required: --title, --criteria, --state, --pointer, --source\n"),
                 (("unknown",), str(parse_error.exception) + "\n"),
                 (("update", "hook-prompt", "--source", "1"), "本会话没有契约\n"),
                 (("open", "release", ""), "目标不能为空\n"))
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                before = self.files()
                code, output, errors = self.command(*arguments)
                self.assertEqual((code, output, errors), (1, "", expected))
                self.assertNotIn("请优先修复本会话契约或 hook 问题，再做别的。", errors)
                self.assertEqual(self.files(), before)

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
                self.root.mkdir()
                self.pid = child.pid
                self.rejects("open", "release", "目标", message="祖先")
                self.assertFalse(list(self.root.glob("-*/*/xy-goal/goal.md")))

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
        goals = list(self.root.glob("-*/*/xy-goal/goal.md"))
        self.assertEqual(len(goals), 1)
        self.assertIn(f'claude_pid: "{result.stdout.splitlines()[0]}"', goals[0].read_text())

    def test_cli_requires_bash_ppid_for_each_owner_command_even_with_claude_pid_environment(self):
        for arguments in (("open", "release", "目标"),
                          ("adopt", str(self.root / "old-contract"))):
            with self.subTest(arguments=arguments):
                before = self.files()
                result = self.cli_command(*arguments)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                for message in ("--claude-pid", "$PPID", "Claude Code", "Bash"):
                    self.assertIn(message, result.stderr)
                self.assertEqual(self.files(), before)

    def test_cli_rejects_invalid_bash_ppid_with_calling_instructions(self):
        for arguments in (("open", "release", "目标"),
                          ("adopt", str(self.root / "old-contract"))):
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
        self.assertFalse((self.inbox).exists())
        self.succeeds("adopt", str(contract))
        another = self.project / "another-contract" / "xy-goal"
        shutil.copytree(contract, another)
        metadata = another / "goal.md"
        metadata.write_text(metadata.read_text().replace("new-session", "another-session"))
        self.rejects("adopt", str(another), message="已有契约")

    def test_same_session_adopt_refreshes_dead_pid_and_blocks_foreign_takeover(self):
        contract = self.open_goal()
        old = subprocess.Popen([sys.executable, "-c", "pass"])
        old.wait()
        metadata = contract / "goal.md"
        metadata.write_text(metadata.read_text().replace(f'"{self.pid}"', f'"{old.pid}"'))
        self.succeeds("check")
        result = self.cli_command("adopt", str(contract), "--claude-pid", str(os.getpid()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'claude_pid: "{os.getpid()}"', metadata.read_text())
        self.session = "takeover-session"
        self.rejects("adopt", str(contract), message="原会话进程还在")
        self.session = "session-main"
        self.succeeds("check")

    def test_same_session_adopt_only_refreshes_pid_even_if_previous_process_is_alive(self):
        contract = self.open_goal()
        self.quote(contract, 1, "保留原事项和映射")
        self.add_task(1)
        before = self.files()
        result = self.cli_command("adopt", str(contract), "--claude-pid", str(os.getpid()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'claude_pid: "{os.getpid()}"', (contract / "goal.md").read_text())
        for relative, content in before.items():
            if relative != str((contract / "goal.md").relative_to(self.root)):
                self.assertEqual(self.files()[relative], content)

    def test_open_refuses_malformed_inbox_before_creating_contract(self):
        inbox = self.inbox
        inbox.mkdir(parents=True)
        (inbox / "bad.md").write_text("坏文件名")
        self.rejects("open", "release", "交付新版本", message=str(inbox / "bad.md"))

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

    @contextmanager
    def readonly_directory(self, path):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        mode = path.stat().st_mode & 0o777
        os.fchmod(descriptor, 0o555)
        try:
            yield
        finally:
            # 即使生产代码已经将目录领取到临时位置，也能恢复测试夹具的权限。
            os.fchmod(descriptor, mode)
            os.close(descriptor)

    def owner_command_with_inbox(self, operation):
        self.root.mkdir(parents=True, exist_ok=True)
        if operation == "open":
            arguments = ("open", "release", "交付新版本")
            contract = self.project / "20261024-093000-release" / "xy-goal"
        elif operation == "adopt":
            contract = self.open_goal()
            old = subprocess.Popen([sys.executable, "-c", "pass"])
            old.wait()
            metadata = contract / "goal.md"
            metadata.write_text(metadata.read_text().replace(f'"{self.pid}"', f'"{old.pid}"'))
            self.session = "new-session"
            arguments = ("adopt", str(contract))
        self.hook("hook-prompt", {"session_id": self.session, "prompt": "只领取一次"})
        return arguments, contract, self.inbox

    def test_committed_owner_commands_warn_on_inbox_delete_failure_without_reimporting(self):
        original_root = self.root
        for operation in ("open", "adopt"):
            with self.subTest(operation=operation):
                self.root = original_root / operation
                self.session = "session-main"
                arguments, contract, inbox = self.owner_command_with_inbox(operation)
                with self.readonly_directory(inbox):
                    code, output, errors = self.command(*arguments)
                    self.assertEqual(code, 0, errors)
                    self.assertEqual(output, f"本次契约：{contract}。按 `~/.claude/skills/xy-goal/SKILL.md`「定时自查」一节做\n")
                    self.assertIn("警告", errors)
                    self.assertFalse(inbox.exists(), "已领原话不能继续留在可被再次领取的位置")
                    self.assertEqual(len(list((contract / "quotes").iterdir())), 1)
                    self.succeeds("adopt", str(contract))
                    self.assertEqual(len(list((contract / "quotes").iterdir())), 1)
                self.hook("hook-prompt", {"session_id": self.session, "prompt": "后续消息"})
                self.assertEqual(len(list((contract / "quotes").iterdir())), 2)

    def test_failed_input_claim_rolls_back_owner_commands_and_can_be_retried(self):
        original_root = self.root
        for operation in ("open", "adopt"):
            with self.subTest(operation=operation):
                self.root = original_root / operation
                self.session = "session-main"
                arguments, contract, inbox = self.owner_command_with_inbox(operation)
                with self.readonly_directory(inbox.parent):
                    self.rejects(*arguments, message="Permission denied")
                self.succeeds(*arguments)
                self.assertFalse(inbox.exists())
                self.assertEqual(len(list((contract / "quotes").iterdir())), 1)

    def test_adopt_write_failure_after_claim_restores_inbox_for_retry(self):
        arguments, contract, inbox = self.owner_command_with_inbox("adopt")
        with self.readonly_directory(contract):
            self.rejects(*arguments, message="Permission denied")
        self.assertEqual((inbox / "1.md").read_text(), "只领取一次")
        self.succeeds(*arguments)
        self.assertFalse(inbox.exists())
        self.assertEqual(len(list((contract / "quotes").iterdir())), 1)

    def test_adopt_refuses_duplicate_contracts_of_the_previous_owner(self):
        contract = self.open_goal()
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        metadata = (contract / "goal.md").read_text()
        (contract / "goal.md").write_text(metadata.replace(f'"{self.pid}"', f'"{child.pid}"'))
        shutil.copytree(contract, self.project / "duplicate-old-owner" / "xy-goal")
        self.session = "new-session"
        self.rejects("adopt", str(contract), message="多份契约")


if __name__ == "__main__":
    unittest.main()
