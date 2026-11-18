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


class GoalTestSupport(unittest.TestCase):

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

    def use_session(self, session):
        self.session = session
        self.session_record(session, self.project_name)

    def transcript_path(self, session=None, project_name=None):
        return (self.projects / (project_name or self.project_name)
                / f"{session or self.session}.jsonl")

    def write_transcript(self, records, *, session=None, project_name=None):
        path = self.transcript_path(session, project_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n"
                                 for record in records), encoding="utf-8")
        return path

    @staticmethod
    def transcript_record(record_type, *, uuid=None, parent=None, timestamp=None,
                          prompt_id=None, origin=None, content=None, subtype=None,
                          is_meta=False, role="user"):
        record = {"type": record_type}
        if subtype is not None:
            record["subtype"] = subtype
        if uuid is not None:
            record.update(uuid=uuid, parentUuid=parent, isSidechain=False)
        if timestamp is not None:
            record["timestamp"] = timestamp
        if prompt_id is not None:
            record["promptId"] = prompt_id
        if origin is not None:
            record["origin"] = {"kind": origin}
        if content is not None:
            record["message"] = {"role": role, "content": content}
        if is_meta:
            record["isMeta"] = True
        return record

    def withdrawn_transcript(self, *, content="中性测试正文", attachment=False):
        records = [
            self.transcript_record(
                "assistant", uuid="assistant", parent="older", timestamp="2026-10-24T08:59:00.000Z",
                content=[], role="assistant"),
            self.transcript_record(
                "system", subtype="stop_hook_summary", uuid="stop", parent="assistant",
                timestamp="2026-10-24T08:59:30.000Z"),
            self.transcript_record(
                "system", subtype="turn_duration", uuid="turn", parent="stop",
                timestamp="2026-10-24T08:59:31.000Z"),
            {"type": "file-history-snapshot", "messageId": "assistant",
             "snapshot": {}, "isSnapshotUpdate": False},
            self.transcript_record(
                "user", uuid="withdrawn", parent="turn", timestamp="2026-10-24T09:00:00.000Z",
                prompt_id="withdrawn-prompt", origin="human", content=content),
            {"type": "api-request", "timestamp": "2026-10-24T09:00:00.100Z"},
            self.transcript_record(
                "system", subtype="away_summary", uuid="away", parent="turn",
                timestamp="2026-10-24T09:00:30.000Z"),
            {"type": "queue-operation", "timestamp": "2026-10-24T09:00:31.000Z"},
            self.transcript_record(
                "system", subtype="scheduled_task_fire", uuid="scheduled", parent="away",
                timestamp="2026-10-24T09:00:40.000Z"),
            self.transcript_record(
                "user", uuid="next-user", parent="scheduled", timestamp="2026-10-24T09:01:00.000Z",
                prompt_id="timer-prompt", content="中性定时提示", is_meta=True),
        ]
        if attachment:
            withdrawn_index = next(index for index, record in enumerate(records)
                                   if record.get("uuid") == "withdrawn")
            records.insert(withdrawn_index + 1, self.transcript_record(
                "attachment", subtype="total_tokens_reminder", uuid="attachment",
                parent="withdrawn", timestamp="2026-10-24T09:00:00.050Z"))
        return records

    @staticmethod
    def set_quote_mtime(path, timestamp, offset=0):
        submitted = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
        os.utime(path, (submitted + offset, submitted + offset))

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

    def rejects_argument_error(self, *arguments, message):
        before = self.files()
        result = self.cli_command(*arguments)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.files(), before, "参数错误不能写契约文件")
        return result.stderr

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

    def open_goal(self, slug):
        self.succeeds("open", slug, "交付新版本")
        return self.project / f"20261024-093000-{slug}" / "xy-goal"

    def make_contract_file_unreadable(self, contract, problem):
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
        return bad_path

    @staticmethod
    def quote(contract, number, text):
        (contract / "quotes" / f"{number}.md").write_text(text, encoding="utf-8")

    def add_task(self, *sources):
        output = self.succeeds("add", "--title", "发布", "--criteria", "测试通过",
                               "--source-kind", "原话直提",
                               "--source", *map(str, sources),
                               "--state", "在办", "--pointer", "无")
        self.assertRegex(output, r"T[1-9][0-9]*", "add 必须创建并返回事项编号")
        return output

    def add_closed_machine_cancel_quote(self, contract):
        self.quote(contract, 1, "发布要求")
        self.add_task(1)
        self.quote(contract, 2, f"本次契约：{contract}。按 xy-goal 的「定时自查」一节做")
        task = contract / "tasks" / "T1.md"
        done = contract / "done" / "T1.md"
        done.write_text(task.read_text(encoding="utf-8") + 'cancel_quote: "2"\n', encoding="utf-8")
        task.unlink()
        (contract / "links.md").write_text("1 T1\n2 T1\n", encoding="utf-8")
        return done

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

    def hook(self, command, payload):
        payload = {"transcript_path": str(self.projects / self.project_name / f"{payload['session_id']}.jsonl"),
                   **payload}
        code, output, errors = self.run_goal(command, session=None, pid=None,
                                             stdin=json.dumps(payload, ensure_ascii=False))
        self.assertEqual(code, 0, errors)
        self.assertEqual(output + errors, "", "成功 hook 不向会话注入正文")

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
            contract = self.open_goal("release")
            old = subprocess.Popen([sys.executable, "-c", "pass"])
            old.wait()
            metadata = contract / "goal.md"
            metadata.write_text(metadata.read_text().replace(f'"{self.pid}"', f'"{old.pid}"'))
            self.session = "new-session"
            arguments = ("adopt", str(contract))
        self.hook("hook-prompt", {"session_id": self.session, "prompt": "只领取一次"})
        return arguments, contract, self.inbox

    def owner_commands_with_inbox(self):
        original_root = self.root
        for operation in ("open", "adopt"):
            self.root = original_root / operation
            self.session = "session-main"
            yield operation, self.owner_command_with_inbox(operation)
