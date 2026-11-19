import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


DELETE_SELF_SCRIPT = Path(__file__).with_name("delete_self.py")
FAKE_CLAUDE_SOURCE = r'''import json
import os
import signal
import sys
import time

fake_claude_directory = os.environ["FAKE_CLAUDE_DIR"]
command_arguments = sys.argv[1:]


def write_marker(name, value=""):
    marker_path = os.path.join(fake_claude_directory, name)
    with open(marker_path, "w", encoding="utf-8") as marker_file:
        marker_file.write(value)


if command_arguments == ["agents", "--json"]:
    write_marker("agents.called")
    sys.stdout.write(os.environ["FAKE_AGENTS_OUTPUT"])
    sys.exit(0)

if command_arguments[:1] == ["rm"]:
    write_marker("rm.called", json.dumps(command_arguments))
    write_marker("rm.pid", str(os.getpid()))
    write_marker("rm.parent", str(os.getppid()))
    process_id = os.getpid()
    ancestor_process_ids = []
    while process_id > 1:
        process_stat = open(f"/proc/{process_id}/stat").read()
        process_id = int(process_stat.rsplit(")", 1)[1].split()[1])
        ancestor_process_ids.append(process_id)
    write_marker("ancestors", " ".join(map(str, ancestor_process_ids)))
    removal_mode = os.environ["FAKE_RM_MODE"]
    if removal_mode == "wait":
        deadline = time.monotonic() + 10
        continue_path = os.path.join(fake_claude_directory, "continue")
        while not os.path.exists(continue_path) and time.monotonic() < deadline:
            time.sleep(0.01)
        if not os.path.exists(continue_path):
            sys.exit(9)
    if removal_mode == "kill":
        os.kill(os.getppid(), signal.SIGKILL)
        sys.exit(0)
    sys.stdout.write(os.environ["FAKE_RM_OUTPUT"])
    sys.stdout.flush()
    if removal_mode == "wait":
        write_marker("done")
    sys.exit(int(os.environ["FAKE_RM_CODE"]))

write_marker("unexpected")
sys.exit(90)
'''


def process_descendants(root_process_id):
    children_by_parent = {}
    for process_path in Path("/proc").iterdir():
        if not process_path.name.isdigit():
            continue
        try:
            process_stat = (process_path / "stat").read_text()
            parent_process_id = int(process_stat.rsplit(")", 1)[1].split()[1])
            process_id = int(process_path.name)
            children_by_parent.setdefault(parent_process_id, []).append(process_id)
        except (OSError, ValueError, IndexError):
            pass
    descendant_process_ids = []
    pending_process_ids = [root_process_id]
    while pending_process_ids:
        parent_process_id = pending_process_ids.pop()
        for child_process_id in children_by_parent.get(parent_process_id, []):
            if child_process_id not in descendant_process_ids:
                descendant_process_ids.append(child_process_id)
                pending_process_ids.append(child_process_id)
    return descendant_process_ids


def stop_and_confirm_processes(process, directory, include_removal_processes=True):
    process_ids = process_descendants(process.pid)[::-1] + [process.pid]
    if include_removal_processes:
        for marker_name in ("rm.pid", "rm.parent"):
            try:
                process_ids.append(int((directory / marker_name).read_text()))
            except (OSError, ValueError):
                pass
    for process_id in dict.fromkeys(process_ids):
        if process_id > 1 and process_id != os.getpid():
            try:
                os.kill(process_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
    process.wait(timeout=3)
    for stream in (process.stdout, process.stderr):
        if stream:
            stream.close()
    deadline = time.monotonic() + 3
    live_process_ids = []
    while time.monotonic() < deadline:
        live_process_ids = []
        for process_id in process_ids:
            try:
                process_stat = Path(f"/proc/{process_id}/stat").read_text()
                process_state = process_stat.rsplit(")", 1)[1].split()[0]
                if process_state != "Z":
                    live_process_ids.append(process_id)
            except OSError:
                pass
        if not live_process_ids:
            break
        time.sleep(0.01)
    assert not live_process_ids, f"测试进程未退出：{live_process_ids}"


class DeleteSelfTest(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.directory = Path(temporary_directory.name)
        (self.directory / "bin").mkdir()
        self.fake_claude = self.directory / "bin/claude"
        interpreter = Path(sys.executable).resolve()
        self.fake_claude.write_text(
            f"#!{interpreter}\n" + FAKE_CLAUDE_SOURCE, encoding="utf-8"
        )
        self.fake_claude.chmod(0o755)
        self.test_path = f"{self.directory}/bin:/usr/bin:/bin"

    def start_delete_self(self, agent_rows=None, script_arguments=(), **settings):
        for marker_path in self.directory.iterdir():
            if marker_path.is_file() and marker_path != self.fake_claude:
                marker_path.unlink()
        environment = os.environ.copy()
        environment.update({
            "PATH": self.test_path,
            "FAKE_CLAUDE_DIR": str(self.directory),
            "FAKE_AGENTS_OUTPUT": json.dumps([] if agent_rows is None else agent_rows),
            "FAKE_RM_MODE": "normal",
            "FAKE_RM_OUTPUT": "removed\n",
            "FAKE_RM_CODE": "0",
        })
        environment.update({key: str(value) for key, value in settings.items()})
        self.assertEqual(
            shutil.which("claude", path=environment["PATH"]), str(self.fake_claude)
        )
        interpreter = str(Path(sys.executable).resolve())
        return subprocess.Popen(
            [interpreter, "-B", str(DELETE_SELF_SCRIPT), *script_arguments],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def run_delete_self(self, agent_rows=None, script_arguments=(), **settings):
        process = self.start_delete_self(agent_rows, script_arguments, **settings)
        try:
            stdout, stderr = process.communicate(timeout=8)
            return process.returncode, stdout, stderr
        finally:
            stop_and_confirm_processes(process, self.directory)

    def matching_agent_rows(self):
        return [{"kind": "background", "pid": os.getpid(), "id": "abc12345"}]

    def test_arguments_are_rejected_without_claude(self):
        return_code, stdout, stderr = self.run_delete_self(script_arguments=("abc12345",))
        self.assertEqual(return_code, 2, stdout + stderr)
        self.assertFalse((self.directory / "agents.called").exists())

    def test_only_one_background_ancestor_is_accepted(self):
        cases = [
            ([{"kind": "background", "pid": 2_000_000_000, "id": "abc12345"}], {}),
            ([{"kind": "other", "pid": os.getpid(), "id": "abc12345"}], {}),
            ([
                {"kind": "background", "pid": os.getpid(), "id": "abc12345"},
                {"kind": "background", "pid": os.getppid(), "id": "def67890"},
            ], {}),
            (None, {"FAKE_AGENTS_OUTPUT": "not JSON"}),
        ]
        for agent_rows, settings in cases:
            return_code, stdout, stderr = self.run_delete_self(agent_rows, **settings)
            self.assertEqual(return_code, 2, stdout + stderr)
            self.assertFalse((self.directory / "rm.called").exists())

    def test_matching_session_runs_rm_after_detaching(self):
        process = self.start_delete_self(self.matching_agent_rows())
        try:
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(stdout, "removed\n")
            called_arguments = json.loads((self.directory / "rm.called").read_text())
            self.assertEqual(called_arguments, ["rm", "abc12345"])
            ancestor_process_ids = map(
                int, (self.directory / "ancestors").read_text().split()
            )
            self.assertNotIn(process.pid, ancestor_process_ids)
        finally:
            stop_and_confirm_processes(process, self.directory)

    def test_detached_rm_survives_session_tree_exit(self):
        process = self.start_delete_self(
            self.matching_agent_rows(),
            FAKE_RM_MODE="wait",
            FAKE_RM_OUTPUT="removed later\n",
        )
        try:
            deadline = time.monotonic() + 4
            while not (self.directory / "rm.pid").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.directory / "rm.pid").exists(), "假 claude rm 没启动")
            stop_and_confirm_processes(process, self.directory, include_removal_processes=False)
            (self.directory / "continue").touch()
            deadline = time.monotonic() + 4
            while not (self.directory / "done").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.directory / "done").exists(), "会话退出杀掉了 claude rm")
        finally:
            stop_and_confirm_processes(process, self.directory)

    def test_rm_failure_and_success_outputs(self):
        return_code, stdout, stderr = self.run_delete_self(
            self.matching_agent_rows(),
            FAKE_RM_OUTPUT="couldn't remove abc12345\n",
            FAKE_RM_CODE=1,
        )
        self.assertEqual(return_code, 1)
        self.assertEqual(stdout, "couldn't remove abc12345\n")
        self.assertIn("claude rm abc12345 没删成，退出码 1", stderr)
        return_code, stdout, stderr = self.run_delete_self(
            self.matching_agent_rows(), FAKE_RM_OUTPUT="removed abc12345\n"
        )
        self.assertEqual((return_code, stdout, stderr), (0, "removed abc12345\n", ""))

    def test_missing_result_has_no_exit_code(self):
        return_code, stdout, stderr = self.run_delete_self(
            self.matching_agent_rows(), FAKE_RM_MODE="kill"
        )
        self.assertEqual(return_code, 1)
        self.assertIn("claude rm abc12345 没送回结果，不知道删没删成", stderr)
        self.assertNotIn("退出码", stderr)


if __name__ == "__main__":
    unittest.main()
