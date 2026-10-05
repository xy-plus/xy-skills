"""check.py 的契约格式与预算测试。"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "check.py"
DEFAULT_NEXT_CHECK = object()
sys.path.insert(0, str(ROOT))
import check as check_module


class CheckScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.contract_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def start_live_child(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(self.stop_child, child)
        return child

    @staticmethod
    def stop_child(child):
        if child.poll() is None:
            child.terminate()
        child.wait()

    def clear_contracts(self):
        for child in self.contract_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    def write_contract(self, name="goal.md", *, entries=None, quotes=None,
                       preamble="", checklist_header=True, quotes_header=True,
                       cron=True, cron_value="123", session="session-1"):
        lines = ["# 测试目标"]
        if cron:
            lines.append(f"cron_job_id: {cron_value}")
        if session is not None:
            lines.append(f"session: {session}")
        if preamble:
            lines.append(preamble)
        if checklist_header:
            lines.append("## 清单")
        lines.extend(entries or [])
        if quotes_header:
            lines.append("## 用户原话")
            for quote in quotes or []:
                lines.extend(["", quote])
        path = self.contract_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def entry(task_id="T3", *, status=" ", criteria="完成验收条件",
              source="原话 7", artifact=None, next_check=DEFAULT_NEXT_CHECK):
        if next_check is DEFAULT_NEXT_CHECK:
            next_check = (datetime.now() + timedelta(hours=11)).strftime(
                "%Y-%m-%d %H:%M",
            )
        if artifact is None:
            artifact = "等：用户：等待输入"
        if (status == " " and artifact.startswith("在跑：")
                and next_check is not None
                and not any(segment.strip().startswith("下次核")
                            for segment in re.split(r"[；;]", artifact))):
            parts = re.split(r"([；;])", artifact, maxsplit=1)
            if len(parts) == 3:
                artifact = parts[0] + parts[1] + f"下次核：{next_check}" + parts[1] + parts[2]
        return [
            f"- [{status}] {task_id} 测试条目",
            f"  - 判据：{criteria}",
            f"  - 出处：{source}",
            f"  - 产物：{artifact}",
        ]

    def test_running_next_check_missing_is_reported_at_artifact_line(self):
        pid = self.start_live_child().pid
        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；src/output.py",
            next_check=None,
        ), quotes=[self.quote()])

        output = self.assert_problem("下次核")
        self.assertIn("goal.md:8: ", output)
        self.assertIn("缺少「下次核」字段", output)
        self.assertEqual(len(output.splitlines()), 1, output)

    def test_running_next_check_rejects_duplicate_misplaced_and_invalid_fields(self):
        pid = self.start_live_child().pid
        invalid_artifacts = (
            (f"在跑：worker（pid {pid}）；下次核：2026-10-05 15:30；"
             "下次核：2026-10-05 16:30",
             "「下次核」格式应为 YYYY-MM-DD HH:MM"),
            (f"在跑：worker（pid {pid}）；src/output.py；"
             "下次核：2026-10-05 15:30",
             "必须写在推进者段之后"),
            (f"在跑：worker（pid {pid}）；下次核：2026-02-30 15:30",
             "不是有效日期时间"),
            (f"在跑：worker（pid {pid}）；下次核：2026-10-05 15:30 +08:00",
             "格式应为 YYYY-MM-DD HH:MM"),
            (f"在跑：worker（pid {pid}）；下次核：2026/10/05 15:30",
             "格式应为 YYYY-MM-DD HH:MM"),
        )
        for artifact, expected in invalid_artifacts:
            with self.subTest(artifact=artifact):
                self.clear_contracts()
                self.write_contract(entries=self.entry(
                    artifact=artifact, next_check=None), quotes=[self.quote()])
                output = self.assert_problem(expected)
                self.assertIn("goal.md:8: ", output)

    def test_expired_next_check_reports_only_contract_deadline(self):
        pid = self.start_live_child().pid
        deadline = "2026-10-05 07:30"
        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；产物指针 src/output.py",
            next_check=deadline,
        ), quotes=[self.quote()])
        now = datetime(2026, 10, 5, 9, 40)

        returncode, stdout, stderr = self._run_check_at(now)

        output = stdout + stderr
        self.assertEqual(returncode, 1, output)
        self.assertIn(
            "goal.md:8: T3：下次核 2026-10-05 07:30 已过期",
            output,
        )

    def test_next_check_comparison_is_strict_in_local_time(self):
        pid = self.start_live_child().pid
        deadline = "2026-10-05 10:00"
        cases = (
            (datetime(2026, 10, 5, 10, 0), 0),
            (datetime(2026, 10, 5, 10, 0, 1), 1),
            (datetime(2026, 10, 5, 9, 59, 59), 0),
        )
        for now, expected in cases:
            with self.subTest(now=now):
                self.write_contract(entries=self.entry(
                    artifact=f"在跑：worker（pid {pid}）；src/output.py",
                    next_check=deadline,
                ), quotes=[self.quote()])
                returncode, stdout, stderr = self._run_check_at(now)
                self.assertEqual(returncode, expected, stdout + stderr)
                if expected == 0:
                    self.assertIn("通过：session=session-1：", stdout)

    def test_next_check_thirteen_hours_ahead_is_rejected(self):
        pid = self.start_live_child().pid
        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；src/output.py",
            next_check="2026-10-05 23:00",
        ), quotes=[self.quote()])
        now = datetime(2026, 10, 5, 10, 0)
        returncode, stdout, stderr = self._run_check_at(now)
        output = stdout + stderr
        self.assertEqual(returncode, 1, output)
        self.assertIn(
            "goal.md:8: T3：下次核离现在超过 12 小时",
            output,
        )

    def test_next_check_exactly_twelve_hours_ahead_is_allowed(self):
        pid = self.start_live_child().pid
        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；src/output.py",
            next_check="2026-10-05 22:00",
        ), quotes=[self.quote()])
        returncode, stdout, stderr = self._run_check_at(datetime(2026, 10, 5, 10, 0))
        self.assertEqual(returncode, 0, stdout + stderr)

    def test_next_check_eleven_hours_ahead_is_allowed(self):
        pid = self.start_live_child().pid
        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；src/output.py",
            next_check="2026-10-05 21:00",
        ), quotes=[self.quote()])
        now = datetime(2026, 10, 5, 10, 0)
        returncode, stdout, stderr = self._run_check_at(now)
        self.assertEqual(returncode, 0, stdout + stderr)
        self.assertIn("通过：session=session-1：", stdout)

    def test_expired_dead_pid_reports_dead_process_and_deadline(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        dead_pid = child.pid
        child.wait()
        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {dead_pid}）；src/output.py",
            next_check="2026-10-05 07:30",
        ), quotes=[self.quote()])
        now = datetime(2026, 10, 5, 9, 40)
        returncode, stdout, stderr = self._run_check_at(now)

        output = stdout + stderr
        self.assertEqual(returncode, 1, output)
        self.assertIn("进程已不在", output)
        self.assertIn("T3：下次核 2026-10-05 07:30 已过期", output)

    def test_waiting_and_acceptance_items_do_not_require_next_check(self):
        entries = self.entry("T1", artifact="等：用户：确认参数")
        entries += self.entry("T2", status="x", source="原话 8",
                              artifact="待验收：reports/accept-T2.md")
        self.write_contract(entries=entries, quotes=[
            self.quote(7, "T1"), self.quote(8, "T2"),
        ])

        self.assert_passes()

    def test_expired_deadline_repeats_until_contract_time_is_updated(self):
        first_pid = self.start_live_child().pid
        second_pid = self.start_live_child().pid
        deadline = "2026-10-05 07:30"
        now = datetime(2026, 10, 5, 9, 40)
        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker-a（pid {first_pid}）；src/output.py",
            next_check=deadline,
        ), quotes=[self.quote()])

        for _ in range(2):
            returncode, stdout, stderr = self._run_check_at(now)
            self.assertEqual(returncode, 1, stdout + stderr)
            self.assertIn("T3：下次核 2026-10-05 07:30 已过期", stdout + stderr)

        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker-b（pid {second_pid}）；new-output.py",
            next_check=deadline,
        ), quotes=[self.quote()])
        returncode, stdout, stderr = self._run_check_at(now)
        self.assertEqual(returncode, 1, stdout + stderr)

        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker-b（pid {second_pid}）；new-output.py",
            next_check="2026-10-05 20:00",
        ), quotes=[self.quote()])
        returncode, stdout, stderr = self._run_check_at(now)
        self.assertEqual(returncode, 0, stdout + stderr)
        self.assertEqual([path.name for path in self.contract_dir.iterdir()], ["goal.md"])

    @staticmethod
    def quote(number=7, landing="T3"):
        return f"（原话 {number} → {landing}）用户要求。"

    def write_ledger(self, name="goal.done.md", lines=()):
        path = self.contract_dir / name
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path

    def run_check(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def _run_check_at(self, now):
        stdout = StringIO()
        stderr = StringIO()
        with mock.patch.object(
                check_module, "_local_now", return_value=now, create=True), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            returncode = check_module.check_directory(self.contract_dir)
        return returncode, stdout.getvalue(), stderr.getvalue()

    def assert_passes(self, *args):
        result = self.run_check(self.contract_dir, *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def assert_problem(self, expected, *args):
        result = self.run_check(self.contract_dir, *args)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        self.assertIn(expected, output)
        self.assertRegex(output, r"[^\n]+:\d+: ")
        return output

    def test_budget_sums_by_session_not_by_file_or_across_sessions(self):
        # 同一 session 的多份契约合并计数，不同 session 分开计数。
        for name, session, filler_char in (("a.md", "same", "甲"),
                                            ("b.md", "same", "乙")):
            fixed = f"# 测试目标\ncron_job_id: 123\nsession: {session}\n\n## 清单\n"
            self.write_contract(name, session=session,
                                preamble=filler_char * (3000 - len(fixed)))
        output = self.assert_problem("session same 的清单部分合计 6000 字，超过 5000 字上限")
        self.assertRegex(output, r"b\.md:\d+: ")

        self.clear_contracts()
        for name, session, filler_char in (("a.md", "one", "甲"),
                                            ("b.md", "two", "乙")):
            fixed = f"# 测试目标\ncron_job_id: 123\nsession: {session}\n\n## 清单\n"
            self.write_contract(name, session=session,
                                preamble=filler_char * (3000 - len(fixed)))
        output = self.assert_passes().stdout
        self.assertIn("session=one", output)
        self.assertIn("session=two", output)
        self.assertIn("a.md 3000 字", output)
        self.assertIn("b.md 3000 字", output)

        self.clear_contracts()
        fixed = "# 测试目标\ncron_job_id: 123\nsession: only\n\n## 清单\n"
        self.write_contract(preamble="甲" * (4000 - len(fixed)), session="only")
        self.assertIn("4000 字", self.assert_passes().stdout)

    def test_four_line_item_over_300_fails_and_exactly_300_passes(self):
        # 每项的标题、判据、出处和产物合计不得超过 300 字。
        target = 300
        rows = self.entry()
        base_size = len("\n".join(rows))
        rows[1] += "字" * (target - base_size)
        self.assertEqual(len("\n".join(rows)), target)
        self.write_contract(entries=rows, quotes=[self.quote()])
        self.assert_passes()

        self.clear_contracts()
        rows[1] += "字"
        self.write_contract("too-long.md", entries=rows, quotes=[self.quote()])
        output = self.assert_problem("条目合计 301 字，超过 300 字")
        self.assertRegex(output, r"too-long\.md:5: ")

    def test_required_criteria_and_source_must_be_nonempty(self):
        # 判据与出处不能只写字段前缀，正文必须非空。
        self.write_contract(entries=self.entry(criteria=""), quotes=[self.quote()])
        output = self.assert_problem("判据：内容不能为空")
        self.assertIn("goal.md:6: ", output)
        self.assertEqual(len(output.splitlines()), 1, output)

        self.clear_contracts()
        self.write_contract(entries=self.entry(source=""),
                            quotes=[self.quote(7, "无：无需对应在办条目")])
        output = self.assert_problem("出处：内容不能为空")
        self.assertIn("goal.md:7: ", output)
        self.assertEqual(len(output.splitlines()), 1, output)

    def test_running_waiting_and_acceptance_prefixes_require_values(self):
        # 「谁在动」字段须包含任务名、条目 ID 或验收产物。
        for artifact, expected in (
            ("在跑：worker", "「在跑：」必须包含进程号（pid N）"),
            ("等：", "「等：」后应为条目 ID 或「用户：…」"),
            ("待验收：", "[x] 「待验收：」后必须有产物指针"),
        ):
            with self.subTest(artifact=artifact):
                self.clear_contracts()
                status = "x" if artifact.startswith("待验收：") else " "
                self.write_contract(entries=self.entry(status=status, artifact=artifact),
                                    quotes=[self.quote()])
                output = self.assert_problem(expected)
                self.assertIn("goal.md:8: ", output)
                if artifact == "在跑：worker":
                    self.assertIn("下次核", output)
                    self.assertEqual(len(output.splitlines()), 2, output)
                else:
                    self.assertEqual(len(output.splitlines()), 1, output)

        self.clear_contracts()
        entries = self.entry(artifact="等：T4")
        entries += self.entry("T4", source="原话 8", artifact="等：用户：确认输入")
        self.write_contract(entries=entries,
                            quotes=[self.quote(7, "T3"), self.quote(8, "T4")])
        self.assert_passes()

        self.clear_contracts()
        self.write_contract(entries=self.entry(status="x", artifact="待验收：reports/check.md"),
                            quotes=[self.quote()])
        self.assert_passes()

    def test_multiline_criteria_count_toward_item_limit_without_format_error(self):
        # 判据换行仍属于同一条目，并计入四行字数预算。
        rows = self.entry()
        rows.insert(2, "    判据的第二行")
        base_size = len("\n".join(rows))
        rows[2] += "字" * (300 - base_size)
        self.assertEqual(len("\n".join(rows)), 300)
        self.write_contract(entries=rows, quotes=[self.quote()])
        self.assert_passes()

        self.clear_contracts()
        rows[2] += "字"
        self.write_contract(entries=rows, quotes=[self.quote()])
        output = self.assert_problem("条目合计 301 字，超过 300 字")
        self.assertNotIn("应为「出处：」", output)

    def test_character_count_matches_wc_m_under_utf8_locale(self):
        # Python len(str) 与 UTF-8 locale 下 wc -m 的字符口径一致。
        sample = "中文abc\n🙂"
        env = dict(os.environ, LC_ALL="C.UTF-8")
        wc = subprocess.run(
            ["wc", "-m"], input=sample, text=True, capture_output=True,
            env=env, check=True,
        )
        self.assertEqual(len(sample), int(wc.stdout.split()[0]))

    def test_artifact_prefixes_wait_targets_and_running_names(self):
        # 检查在跑、等、待验收前缀；等待目标必须仍在清单中。
        live_pid = self.start_live_child().pid
        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker-a（pid {live_pid}）；产物路径"),
                            quotes=[self.quote()])
        output = self.assert_passes().stdout
        self.assertIn(f"在跑：worker-a（pid {live_pid}）", output)

        self.clear_contracts()
        self.write_contract(entries=self.entry(artifact="等：用户：确认参数"),
                            quotes=[self.quote()])
        self.assert_passes()

        self.clear_contracts()
        entries = self.entry(artifact=f"在跑：worker-a（pid {live_pid}）；产物")
        entries += self.entry("T4", source="原话 8",
                              artifact=f"在跑：worker-b（pid {live_pid}）；产物")
        self.write_contract(entries=entries,
                            quotes=[self.quote(7, "T3"), self.quote(8, "T4")])
        output = self.assert_passes().stdout
        self.assertIn("worker-a", output)
        self.assertIn("worker-b", output)

        self.clear_contracts()
        self.write_contract(entries=self.entry(artifact="报告路径"), quotes=[self.quote()])
        output = self.assert_problem("缺少「谁在动」前缀")
        self.assertIn("goal.md:8: ", output)

        self.clear_contracts()
        self.write_contract(entries=self.entry(status="x", artifact="已完成"),
                            quotes=[self.quote()])
        self.assert_problem("[x] 产物栏必须以「待验收：」开头")

        self.clear_contracts()
        self.write_contract(entries=self.entry(status="x", artifact="待验收：报告路径"),
                            quotes=[self.quote()])
        self.assert_passes()

    def test_live_non_ancestor_child_pid_is_checked_and_included_in_summary(self):
        pid = self.start_live_child().pid
        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；产物路径"), quotes=[self.quote()])

        result = self.assert_passes()

        self.assertIn(f"在跑：worker（pid {pid}）", result.stdout)

    def test_executing_process_pid_is_accepted_as_running(self):
        pid = os.getpid()
        self.write_contract(entries=self.entry(
            artifact=f"在跑：执行者（pid {pid}）；产物路径"),
                            quotes=[self.quote()])
        real_readlink = os.readlink

        def identify_current_process(path, *args, **kwargs):
            if os.fspath(path) == f"/proc/{pid}/exe":
                return "/usr/bin/claude"
            return real_readlink(path, *args, **kwargs)

        stdout = StringIO()
        stderr = StringIO()
        with mock.patch.object(
                check_module.os, "readlink",
                side_effect=identify_current_process) as readlink, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            returncode = check_module.check_directory(self.contract_dir)

        self.assertEqual(returncode, 0, stdout.getvalue() + stderr.getvalue())
        self.assertIn(f"在跑：执行者（pid {pid}）", stdout.getvalue())
        readlink.assert_not_called()

    def test_permission_error_while_checking_running_pid_counts_as_alive(self):
        pid = 2147483647
        self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；产物路径"), quotes=[self.quote()])
        stdout = StringIO()
        stderr = StringIO()

        with mock.patch.object(check_module.os, "kill", side_effect=PermissionError), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            returncode = check_module.check_directory(self.contract_dir)

        self.assertEqual(returncode, 0, stderr.getvalue())
        self.assertIn(f"在跑：worker（pid {pid}）", stdout.getvalue())

    def test_exited_running_process_is_reported_at_artifact_line(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        dead_pid = child.pid
        child.wait()
        self.write_contract(entries=self.entry(
            artifact=f"在跑：child（pid {dead_pid}）；产物路径"), quotes=[self.quote()])

        result = self.run_check(self.contract_dir)
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1, output)
        self.assertEqual(
            output.strip(),
            f"goal.md:8: 在跑：child（pid {dead_pid}）的进程已不在：收尾、送验或接手",
        )

    def test_running_entry_without_pid_reports_required_pid_at_artifact_line(self):
        self.write_contract(entries=self.entry(
            artifact="在跑：worker；产物路径"), quotes=[self.quote()])

        result = self.run_check(self.contract_dir)
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1, output)
        self.assertEqual(
            output.strip(),
            "goal.md:8: 「在跑：」必须包含进程号（pid N）",
        )

    def test_only_exited_pid_is_reported_when_running_entry_lists_two(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        dead_pid = child.pid
        child.wait()
        live_pid = self.start_live_child().pid
        self.write_contract(entries=self.entry(
            artifact=(f"在跑：live（pid {live_pid}）、child（pid {dead_pid}）；产物路径")),
            quotes=[self.quote()])

        result = self.run_check(self.contract_dir)
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1, output)
        self.assertEqual(
            output.strip(),
            f"goal.md:8: 在跑：child（pid {dead_pid}）的进程已不在：收尾、送验或接手",
        )

    def test_waiting_for_missing_or_closed_task_fails_and_waiting_for_open_task_passes(self):
        # 等：只能指向清单中仍开放的条目。
        self.write_contract(entries=self.entry(artifact="等：T9"), quotes=[self.quote()])
        output = self.assert_problem("等：T9 指向不在清单中的条目")
        self.assertIn("goal.md:8: ", output)

        self.clear_contracts()
        entries = self.entry(artifact="等：T9；等 T9 的产物")
        entries += self.entry("T9", source="原话 8")
        self.write_contract(entries=entries,
                            quotes=[self.quote(7, "T3"), self.quote(8, "T9")])
        self.assert_passes()

        self.clear_contracts()
        self.write_contract(entries=self.entry(artifact="等：T9"), quotes=[self.quote()])
        self.write_ledger(lines=["- T9 已验收（产物路径；验收 reports/accept-T9.md）"])
        self.assert_problem("等：T9 指向不在清单中的条目")

    def test_wait_chain_can_end_at_a_live_worker(self):
        # 等待链可以经过开放条目到达仍在工作的非主会话推进者。
        worker_pid = self.start_live_child().pid
        entries = self.entry("T1", artifact="等：T2")
        entries += self.entry(
            "T2", source="原话 8",
            artifact=f"在跑：worker（pid {worker_pid}）；产物路径",
        )
        self.write_contract(entries=entries,
                            quotes=[self.quote(7, "T1"), self.quote(8, "T2")])

        self.assert_passes()

    def test_wait_chain_can_end_at_a_user_decision(self):
        # 等待链可以经过开放条目终止于待用户决定的事项。
        entries = self.entry("T1", artifact="等：T2")
        entries += self.entry("T2", source="原话 8", artifact="等：用户：确认参数")
        self.write_contract(entries=entries,
                            quotes=[self.quote(7, "T1"), self.quote(8, "T2")])

        self.assert_passes()

    def test_wait_cycle_reports_every_task_in_the_cycle(self):
        # 等待关系成环时，错误应明确列出环上的每个条目。
        entries = self.entry("T1", artifact="等：T2")
        entries += self.entry("T2", source="原话 8", artifact="等：T1")
        self.write_contract(entries=entries,
                            quotes=[self.quote(7, "T1"), self.quote(8, "T2")])

        output = self.assert_problem("等待链成环：T1 → T2 → T1")
        self.assertIn("goal.md:8: ", output)

    def test_self_wait_is_reported_as_a_cycle(self):
        # 条目等待自己也应被识别为等待环。
        self.write_contract(entries=self.entry(artifact="等：T3"), quotes=[self.quote()])

        output = self.assert_problem("等待链成环：T3 → T3")
        self.assertIn("goal.md:8: ", output)

    def test_wait_list_accepts_multiple_open_targets_across_acyclic_branches(self):
        # 多个开放目标都是等待边，各分支可分别终止于推进者或用户。
        worker_pid = self.start_live_child().pid
        entries = self.entry("T1", artifact="等：T2，T3")
        entries += self.entry(
            "T2", source="原话 8",
            artifact=f"在跑：worker（pid {worker_pid}）；产物路径",
        )
        entries += self.entry("T3", source="原话 9", artifact="等：用户：确认参数")
        self.write_contract(entries=entries, quotes=[
            self.quote(7, "T1"), self.quote(8, "T2"), self.quote(9, "T3"),
        ])

        self.assert_passes()

    def test_wait_list_rejects_a_closed_target_even_when_another_target_is_open(self):
        # 逗号列出的每个目标都必须仍在清单中，不能漏掉后面的关闭目标。
        entries = self.entry("T1", artifact="等：T2，T9")
        entries += self.entry("T2", source="原话 8", artifact="等：用户：确认参数")
        self.write_contract(entries=entries,
                            quotes=[self.quote(7, "T1"), self.quote(8, "T2")])
        self.write_ledger(lines=["- T9 已验收（产物路径；验收 reports/accept-T9.md）"])

        output = self.assert_problem("等：T9 指向不在清单中的条目")
        self.assertIn("goal.md:8: ", output)

    def test_cycle_through_second_wait_target_is_reported(self):
        # 每个目标都是图边，第二个目标形成的环也必须被发现。
        entries = self.entry("T1", artifact="等：T2, T3")
        entries += self.entry("T2", source="原话 8", artifact="等：用户：确认参数")
        entries += self.entry("T3", source="原话 9", artifact="等：T1")
        self.write_contract(entries=entries, quotes=[
            self.quote(7, "T1"), self.quote(8, "T2"), self.quote(9, "T3"),
        ])

        output = self.assert_problem("等待链成环")
        self.assertIn("T1", output)
        self.assertIn("T3", output)
        self.assertIn("goal.md:8: ", output)

    def test_waiting_for_an_acceptance_pending_task_is_a_valid_terminal(self):
        # [x] 待验收事项仍在清单中，可以作为等待链的终点。
        entries = self.entry("T1", artifact="等：T2")
        entries += self.entry("T2", status="x", source="原话 8",
                              artifact="待验收：reports/accept-T2.md")
        self.write_contract(entries=entries,
                            quotes=[self.quote(7, "T1"), self.quote(8, "T2")])

        self.assert_passes()

    def test_original_quote_requires_a_landing_but_accepts_rule_and_none_landings(self):
        # 原话标注必须说明落到条目、规矩或无落点。
        self.write_contract(entries=[], quotes=["（原话 7 → ）用户要求。"])
        self.assert_problem("原话 7 缺少落点")

        for landing in ("规矩：记忆 foo", "无：状态查询已回答"):
            with self.subTest(landing=landing):
                self.clear_contracts()
                self.write_contract("valid.md", entries=[],
                                    quotes=[self.quote(7, landing)])
                self.assert_passes()

    def test_source_ranges_expand_and_every_referenced_quote_must_exist(self):
        # 出处区间展开后，其中每个原话编号都必须存在。
        rows = self.entry(source="原话 113～115、120")
        quotes = [self.quote(n) for n in (113, 115, 120)]
        self.write_contract(entries=rows, quotes=quotes)
        output = self.assert_problem("出处引用的原话 114 不存在")
        self.assertIn("goal.md:7: ", output)

        self.clear_contracts()
        self.write_contract("complete-range.md", entries=rows,
                            quotes=[self.quote(n) for n in (113, 114, 115, 120)])
        self.assert_passes()

    def test_landing_to_active_item_must_match_its_source_but_ledger_items_are_allowed(self):
        # 在办条目的原话落点须与出处对应；已入账条目不必留在清单。
        self.write_contract(entries=self.entry(source="原话 8"),
                            quotes=[self.quote(8, "T3"), self.quote(9, "T3")])
        self.assert_problem("原话 9 落到 T3，但 T3 的出处没有原话 9")

        self.clear_contracts()
        self.write_contract("in-ledger.md", entries=[], quotes=[self.quote(9, "T2")])
        self.write_ledger("in-ledger.done.md", lines=[
            "- T2 已验收目标（产物路径；验收 reports/accept-T2.md）",
        ])
        self.assert_passes()

        self.clear_contracts()
        self.write_contract("cancelled-ledger.md", entries=[], quotes=[
            self.quote(9, "T2"), self.quote(10, "T3"),
        ])
        self.write_ledger("cancelled-ledger.done.md", lines=[
            "- T2 已验收目标（产物路径；验收 reports/accept-T2.md）",
            "- [~] T3 用户取消（原话 10：「不做」）",
        ])
        self.assert_passes()

        self.clear_contracts()
        self.write_contract("unknown.md", entries=[], quotes=[self.quote(9, "T404")])
        self.write_ledger("unknown.done.md", lines=[
            "- T5 已验收其他目标（产物路径；验收 reports/accept-T5.md）",
        ])
        self.write_ledger("other.done.md", lines=[
            "- T404 已验收同名条目（产物路径；验收 reports/accept-T404.md）",
        ])
        output = self.assert_problem("原话 9 的落点 T404 不在清单也不在账本")
        self.assertIn("unknown.md:7: ", output)

    def test_landing_categories_require_descriptions_and_do_not_extract_ids_from_them(self):
        # 分号分隔不同落点；规矩与无的说明须非空，其中的 T 编号不算条目落点。
        for landing in ("规矩：", "无："):
            with self.subTest(landing=landing):
                self.clear_contracts()
                self.write_contract(entries=[], quotes=[self.quote(7, landing)])
                expected = f"{landing[:3]}后的说明不能为空"
                self.assert_problem(expected)

        self.clear_contracts()
        self.write_contract(entries=self.entry(source="原话 8"), quotes=[
            self.quote(7, "无：T3 已关闭"), self.quote(8, "T3"),
        ])
        self.assert_passes()

        self.clear_contracts()
        self.write_contract(entries=self.entry(source="原话 9"),
                            quotes=[self.quote(9, "T3；规矩：记忆 foo；无：已答")])
        self.assert_passes()

    def test_required_template_headers_cron_and_session(self):
        # 契约头和清单、用户原话两个必要区段都存在。
        self.write_contract(entries=[], quotes=[])
        self.assert_passes()

        self.clear_contracts()
        self.write_contract("no-list.md", entries=[], quotes=[], checklist_header=False)
        self.assert_problem("缺少「## 清单」标题")

        self.clear_contracts()
        self.write_contract("no-quotes.md", entries=[], quotes=[], quotes_header=False)
        self.assert_problem("缺少「## 用户原话」标题")

        self.clear_contracts()
        self.write_contract("no-cron.md", entries=[], quotes=[], cron=False)
        self.assert_problem("缺少 cron_job_id: 行")

        self.clear_contracts()
        self.write_contract("no-session.md", entries=[], quotes=["session: body text"],
                            session=None)
        self.assert_problem("缺少 session: 行")

        self.clear_contracts()
        self.write_contract("empty-cron.md", entries=[], quotes=[], cron_value="")
        output = self.assert_problem("cron_job_id: 值不能为空")
        self.assertIn("empty-cron.md:2: ", output)
        self.assertEqual(len(output.splitlines()), 1, output)

    def test_each_session_has_one_contract_but_distinct_sessions_are_allowed(self):
        # 一个 session 只允许一份契约，这条规则独立于 5000 字预算。
        self.write_contract("a.md", entries=[], quotes=[], session="shared")
        self.write_contract("b.md", entries=[], quotes=[], session="shared")
        output = self.assert_problem(
            "session shared 已有另一份契约 b.md，一个会话只许一份"
        )
        self.assertIn("a.md:1: ", output)
        self.assertIn("session shared 已有另一份契约 a.md，一个会话只许一份", output)
        self.assertIn("b.md:1: ", output)
        self.assertEqual(len(output.splitlines()), 2, output)
        self.assertLess(len((self.contract_dir / "a.md").read_text(encoding="utf-8"))
                        + len((self.contract_dir / "b.md").read_text(encoding="utf-8")), 5000)

        self.clear_contracts()
        self.write_contract("a.md", entries=[], quotes=[], session="one")
        self.write_contract("b.md", entries=[], quotes=[], session="two")
        output = self.assert_passes().stdout
        self.assertIn("session=one", output)
        self.assertIn("session=two", output)

    def test_original_text_mention_and_multiline_criteria_are_not_misparsed(self):
        # 原话正文里的相似字样不是段首标注，跨行判据照样计数。
        rows = self.entry()
        rows.insert(2, "    判据第二行")
        self.write_contract(entries=rows, quotes=[
            self.quote(7, "T3"),
            "正文里提到「（原话 3 说过旧口径）」但这不是标注。",
        ])
        self.assert_passes()

    def test_done_ledgers_require_one_of_the_two_closed_item_forms(self):
        # 账本只接纳验收记录与有原话依据的取消记录，且不计入预算。
        self.write_contract(entries=[], quotes=[])
        self.write_ledger(lines=[
            "- T1 已验收目标（产物路径；验收 reports/accept-T1.md）",
            "- [~] T2 用户取消（原话 9：「不做」）",
        ])
        self.assert_passes()

        self.clear_contracts()
        self.write_contract(entries=[], quotes=[])
        self.write_ledger(lines=["- [ ] T5 还在办（报告路径）"])
        output = self.assert_problem("账本格式错误")
        self.assertIn("goal.done.md:1: ", output)

        self.clear_contracts()
        self.write_contract(entries=[], quotes=[])
        self.write_ledger(lines=[""])
        self.assert_problem("账本格式错误（不允许空行）")

        self.clear_contracts()
        self.write_contract(entries=[], quotes=[])
        self.write_ledger(lines=["- T6 一句话（没有验收或原话来源）"])
        self.assert_problem("账本格式错误")

    def test_only_top_level_contracts_are_checked_and_archive_and_ledgers_are_not_budgeted(self):
        # 只读目录顶层契约；archive 与账本不参加 session 预算。
        self.write_contract("active.md", preamble="活" * 100)
        self.write_contract("archive/old.md", preamble="旧" * 6000)
        self.write_contract("nested/ignored.md", preamble="嵌" * 6000)
        self.write_ledger(lines=["- T1 已验收目标（产物路径；验收 reports/accept.md）"])
        result = self.assert_passes()
        self.assertIn("active.md", result.stdout)
        self.assertNotIn("old.md", result.stdout)
        self.assertNotIn("ignored.md", result.stdout)
        self.assertNotIn("goal.done.md", result.stdout)

    def test_missing_argument_exits_with_usage_code_2(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("必须显式提供契约目录", result.stderr)


if __name__ == "__main__":
    unittest.main()
