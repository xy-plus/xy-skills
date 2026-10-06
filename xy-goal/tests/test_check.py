"""check.py 的测试：一个用例守一条规则。

运行：cd xy-goal && python3 -B -m unittest discover -s tests
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "check.py"
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

    def write_contract(self, name="goal.md", *, entries, checklist_header=True,
                       cron=True, cron_value="123", session="session-1"):
        lines = ["# 测试目标"]
        if cron:
            lines.append(f"cron_job_id: {cron_value}")
        if session is not None:
            lines.append(f"session: {session}")
        if checklist_header:
            lines.append("## 清单")
        lines.extend(entries)
        path = self.contract_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_quotes(self, contract_path, quotes):
        """原话文件名由契约路径推出，和 check.py 的 _quotes_path 同一条规则。"""
        path = contract_path.with_name(contract_path.stem + ".quotes.md")
        path.write_text("\n\n".join(quotes) + ("\n" if quotes else ""), encoding="utf-8")
        return path

    @staticmethod
    def entry(task_id="T3", *, status=" ", criteria="完成验收条件",
              source="原话 1", artifact):
        return [
            f"- [{status}] {task_id} 测试条目",
            f"  - 判据：{criteria}",
            f"  - 出处：{source}",
            f"  - 产物：{artifact}",
        ]

    @staticmethod
    def later():
        return (datetime.now() + timedelta(hours=11)).strftime("%Y-%m-%d %H:%M")

    def entry_of_length(self, length):
        """四行合计恰好 length 字的条目（标题行到产物行，含行间换行），用判据补齐长度。"""
        artifact = f"等：用户：等待输入；下次核：{self.later()}"
        filler = length - len("\n".join(self.entry(criteria="", artifact=artifact)))
        return self.entry(criteria="判" * filler, artifact=artifact)

    def test_running_next_check_missing_is_reported_at_artifact_line(self):
        pid = self.start_live_child().pid
        path = self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；src/output.py"))
        self.write_quotes(path, [self.quote()])

        output = self.assert_problem("下次核")
        self.assertIn("goal.md:8: ", output)
        self.assertIn("缺少「下次核」字段", output)
        self.assertEqual(len(output.splitlines()), 1, output)

    def test_running_next_check_rejects_duplicate_misplaced_and_invalid_fields(self):
        pid = self.start_live_child().pid
        invalid_artifacts = (
            (f"在跑：worker（pid {pid}）；下次核：2026-10-05 15:30；"
             "下次核：2026-10-05 16:30",
             "「下次核」必须是产物栏第二段且只有一个"),
            (f"在跑：worker（pid {pid}）；src/output.py；"
             "下次核：2026-10-05 15:30",
             "「下次核」必须是产物栏第二段且只有一个"),
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
                path = self.write_contract(entries=self.entry(artifact=artifact))
                self.write_quotes(path, [self.quote()])
                output = self.assert_problem(expected)
                self.assertIn("goal.md:8: ", output)

    def test_expired_next_check_reports_only_contract_deadline(self):
        pid = self.start_live_child().pid
        path = self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；下次核：2026-10-05 07:30；产物指针 src/output.py"))
        self.write_quotes(path, [self.quote()])
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
        cases = (
            (datetime(2026, 10, 5, 10, 0), 0),
            (datetime(2026, 10, 5, 10, 0, 1), 1),
            (datetime(2026, 10, 5, 9, 59, 59), 0),
        )
        for now, expected in cases:
            with self.subTest(now=now):
                path = self.write_contract(entries=self.entry(
                    artifact=f"在跑：worker（pid {pid}）；下次核：2026-10-05 10:00；src/output.py"))
                self.write_quotes(path, [self.quote()])
                returncode, stdout, stderr = self._run_check_at(now)
                self.assertEqual(returncode, expected, stdout + stderr)
                if expected == 0:
                    self.assertIn("通过：session=session-1：", stdout)

    def test_next_check_thirteen_hours_ahead_is_rejected(self):
        pid = self.start_live_child().pid
        path = self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；下次核：2026-10-05 23:00；src/output.py"))
        self.write_quotes(path, [self.quote()])
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
        path = self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；下次核：2026-10-05 22:00；src/output.py"))
        self.write_quotes(path, [self.quote()])
        returncode, stdout, stderr = self._run_check_at(datetime(2026, 10, 5, 10, 0))
        self.assertEqual(returncode, 0, stdout + stderr)

    def test_next_check_eleven_hours_ahead_is_allowed(self):
        pid = self.start_live_child().pid
        path = self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；下次核：2026-10-05 21:00；src/output.py"))
        self.write_quotes(path, [self.quote()])
        now = datetime(2026, 10, 5, 10, 0)
        returncode, stdout, stderr = self._run_check_at(now)
        self.assertEqual(returncode, 0, stdout + stderr)
        self.assertIn("通过：session=session-1：", stdout)

    def test_expired_dead_pid_reports_dead_process_and_deadline(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        dead_pid = child.pid
        child.wait()
        path = self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {dead_pid}）；下次核：2026-10-05 07:30；src/output.py"))
        self.write_quotes(path, [self.quote()])
        now = datetime(2026, 10, 5, 9, 40)
        returncode, stdout, stderr = self._run_check_at(now)

        output = stdout + stderr
        self.assertEqual(returncode, 1, output)
        self.assertIn("进程已不在", output)
        self.assertIn("T3：下次核 2026-10-05 07:30 已过期", output)

    def test_every_open_item_needs_a_next_check(self):
        for status, artifact in ((" ", "等：用户：确认参数"), (" ", "等：T4"),
                                 ("x", "待验收：reports/a.md")):
            with self.subTest(artifact=artifact):
                self.clear_contracts()
                entries = self.entry(status=status, artifact=artifact)
                quotes = [self.quote(1, "T3")]
                if "T4" in artifact:
                    entries += self.entry("T4", source="原话 2",
                                          artifact=f"等：用户：x；下次核：{self.later()}")
                    quotes.append(self.quote(2, "T4"))
                path = self.write_contract(entries=entries)
                self.write_quotes(path, quotes)
                output = self.assert_problem("缺少「下次核」字段")
                self.assertIn("goal.md:8: ", output)

    def test_running_entry_names_exactly_one_pusher(self):
        live = self.start_live_child().pid
        path = self.write_contract(entries=self.entry(
            artifact=f"在跑：a（pid {live}）、b（pid {live}）；下次核：{self.later()}；产物"))
        self.write_quotes(path, [self.quote()])
        self.assert_problem("「在跑：」只写一个推进者，格式「名字（pid N）」")

    def test_wait_targets_use_the_enumeration_comma(self):
        later = self.later()
        entries = self.entry("T1", artifact=f"等：T2、T3；下次核：{later}")
        entries += self.entry("T2", source="原话 2", artifact=f"等：用户：a；下次核：{later}")
        entries += self.entry("T3", source="原话 3", artifact=f"等：用户：b；下次核：{later}")
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [self.quote(1, "T1"), self.quote(2, "T2"), self.quote(3, "T3")])
        self.assert_passes()

        self.clear_contracts()
        entries = self.entry("T1", artifact=f"等：T2，T3；下次核：{later}")
        entries += self.entry("T2", source="原话 2", artifact=f"等：用户：a；下次核：{later}")
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [self.quote(1, "T1"), self.quote(2, "T2")])
        self.assert_problem("「等：」后应为条目 ID 或「用户：…」")

    @staticmethod
    def quote(number=1, landing="T3"):
        return f"（原话 {number} → {landing}）用户要求。"

    def write_ledger(self, name="goal.done.md", lines=()):
        path = self.contract_dir / name
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path

    def run_check(self, arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def _run_check_at(self, now):
        stdout = StringIO()
        stderr = StringIO()
        with mock.patch.object(check_module, "datetime", wraps=datetime) as fake, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            fake.now.return_value = now
            returncode = check_module.check_directory(self.contract_dir)
        return returncode, stdout.getvalue(), stderr.getvalue()

    def assert_passes(self):
        result = self.run_check([self.contract_dir])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def assert_problem(self, expected):
        result = self.run_check([self.contract_dir])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        self.assertIn(expected, output)
        self.assertRegex(output, r"[^\n]+:\d+: ")
        return output

    def test_required_criteria_and_source_must_be_nonempty(self):
        # 判据与出处不能只写字段前缀，正文必须非空。
        later = self.later()
        path = self.write_contract(entries=self.entry(
            criteria="", artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, [self.quote()])
        output = self.assert_problem("判据：内容不能为空")
        self.assertIn("goal.md:6: ", output)
        self.assertEqual(len(output.splitlines()), 1, output)

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(
            source="", artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, [self.quote(1, "无：无需对应在办条目")])
        output = self.assert_problem("出处：内容不能为空")
        self.assertIn("goal.md:7: ", output)
        self.assertEqual(len(output.splitlines()), 1, output)

    def test_running_waiting_and_acceptance_prefixes_require_values(self):
        # 「谁在动」字段须包含任务名、条目 ID 或验收产物。
        for artifact, expected in (
            ("在跑：worker", "「在跑：」只写一个推进者，格式「名字（pid N）」"),
            ("等：", "「等：」后应为条目 ID 或「用户：…」"),
            ("待验收：", "[x] 「待验收：」后必须有产物指针"),
        ):
            with self.subTest(artifact=artifact):
                self.clear_contracts()
                status = "x" if artifact.startswith("待验收：") else " "
                path = self.write_contract(entries=self.entry(status=status, artifact=artifact))
                self.write_quotes(path, [self.quote()])
                output = self.assert_problem(expected)
                self.assertIn("goal.md:8: ", output)
                self.assertIn("缺少「下次核」字段", output)
                self.assertEqual(len(output.splitlines()), 2, output)

        self.clear_contracts()
        later = self.later()
        entries = self.entry(artifact=f"等：T4；下次核：{later}")
        entries += self.entry("T4", source="原话 2", artifact=f"等：用户：确认输入；下次核：{later}")
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [self.quote(1, "T3"), self.quote(2, "T4")])
        self.assert_passes()

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(
            status="x", artifact=f"待验收：reports/check.md；下次核：{later}"))
        self.write_quotes(path, [self.quote()])
        self.assert_passes()

    def test_artifact_prefixes_wait_targets_and_running_names(self):
        # 检查在跑、等、待验收前缀；等待目标必须仍在清单中。
        live_pid = self.start_live_child().pid
        later = self.later()
        path = self.write_contract(entries=self.entry(
            artifact=f"在跑：worker-a（pid {live_pid}）；下次核：{later}；产物路径"))
        self.write_quotes(path, [self.quote()])
        output = self.assert_passes().stdout
        self.assertEqual(output.strip(),
                         f"通过：session=session-1：goal.md；在跑：worker-a（pid {live_pid}）")

        self.clear_contracts()
        entries = self.entry(artifact=f"在跑：worker-a（pid {live_pid}）；下次核：{later}；产物")
        entries += self.entry("T4", source="原话 2",
                              artifact=f"在跑：worker-b（pid {live_pid}）；下次核：{later}；产物")
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [self.quote(1, "T3"), self.quote(2, "T4")])
        output = self.assert_passes().stdout
        self.assertIn("worker-a", output)
        self.assertIn("worker-b", output)

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(artifact="报告路径"))
        self.write_quotes(path, [self.quote()])
        output = self.assert_problem("缺少「谁在动」前缀")
        self.assertIn("goal.md:8: ", output)

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(status="x", artifact="已完成"))
        self.write_quotes(path, [self.quote()])
        self.assert_problem("[x] 产物栏必须以「待验收：」开头")

    def test_permission_error_while_checking_running_pid_counts_as_alive(self):
        pid = 2147483647
        path = self.write_contract(entries=self.entry(
            artifact=f"在跑：worker（pid {pid}）；下次核：{self.later()}；产物路径"))
        self.write_quotes(path, [self.quote()])
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
        path = self.write_contract(entries=self.entry(
            artifact=f"在跑：child（pid {dead_pid}）；下次核：{self.later()}；产物路径"))
        self.write_quotes(path, [self.quote()])

        result = self.run_check([self.contract_dir])
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1, output)
        self.assertEqual(
            output.strip(),
            f"goal.md:8: 在跑：child（pid {dead_pid}）的进程已不在：收尾或送验",
        )

    def test_running_entry_without_pid_is_rejected_at_artifact_line(self):
        path = self.write_contract(entries=self.entry(
            artifact=f"在跑：worker；下次核：{self.later()}；产物路径"))
        self.write_quotes(path, [self.quote()])

        result = self.run_check([self.contract_dir])
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1, output)
        self.assertEqual(
            output.strip(),
            "goal.md:8: 「在跑：」只写一个推进者，格式「名字（pid N）」",
        )

    def test_waiting_for_missing_or_closed_task_fails_and_waiting_for_open_task_passes(self):
        # 等：只能指向清单中仍开放的条目。
        later = self.later()
        path = self.write_contract(entries=self.entry(artifact=f"等：T9；下次核：{later}"))
        self.write_quotes(path, [self.quote()])
        output = self.assert_problem("等：T9 指向不在清单中的条目")
        self.assertIn("goal.md:8: ", output)

        self.clear_contracts()
        entries = self.entry(artifact=f"等：T9；下次核：{later}；等 T9 的产物")
        entries += self.entry("T9", source="原话 2", artifact=f"等：用户：等待输入；下次核：{later}")
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [self.quote(1, "T3"), self.quote(2, "T9")])
        self.assert_passes()

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(artifact=f"等：T9；下次核：{later}"))
        self.write_quotes(path, [self.quote()])
        self.write_ledger(lines=["- T9 已验收（产物路径；验收 reports/accept-T9.md）"])
        self.assert_problem("等：T9 指向不在清单中的条目")

    def test_wait_chain_can_end_at_a_live_worker(self):
        # 等待链可以经过开放条目到达仍在工作的非主会话推进者。
        worker_pid = self.start_live_child().pid
        later = self.later()
        entries = self.entry("T1", artifact=f"等：T2；下次核：{later}")
        entries += self.entry(
            "T2", source="原话 2",
            artifact=f"在跑：worker（pid {worker_pid}）；下次核：{later}；产物路径",
        )
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [self.quote(1, "T1"), self.quote(2, "T2")])

        self.assert_passes()

    def test_wait_chain_can_end_at_a_user_decision(self):
        # 等待链可以经过开放条目终止于待用户决定的事项。
        later = self.later()
        entries = self.entry("T1", artifact=f"等：T2；下次核：{later}")
        entries += self.entry("T2", source="原话 2", artifact=f"等：用户：确认参数；下次核：{later}")
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [self.quote(1, "T1"), self.quote(2, "T2")])

        self.assert_passes()

    def test_wait_cycle_reports_every_task_in_the_cycle(self):
        # 等待关系成环时，错误应明确列出环上的每个条目。
        later = self.later()
        entries = self.entry("T1", artifact=f"等：T2；下次核：{later}")
        entries += self.entry("T2", source="原话 2", artifact=f"等：T1；下次核：{later}")
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [self.quote(1, "T1"), self.quote(2, "T2")])

        output = self.assert_problem("等待链成环：T1 → T2 → T1")
        self.assertEqual(output.strip(), "goal.md:8: 等待链成环：T1 → T2 → T1")

    def test_self_wait_is_reported_as_a_cycle(self):
        # 条目等待自己也应被识别为等待环。
        path = self.write_contract(entries=self.entry(artifact=f"等：T3；下次核：{self.later()}"))
        self.write_quotes(path, [self.quote()])

        output = self.assert_problem("等待链成环：T3 → T3")
        self.assertIn("goal.md:8: ", output)

    def test_wait_list_accepts_multiple_open_targets_across_acyclic_branches(self):
        # 多个开放目标都是等待边，各分支可分别终止于推进者或用户。
        worker_pid = self.start_live_child().pid
        later = self.later()
        entries = self.entry("T1", artifact=f"等：T2、T3；下次核：{later}")
        entries += self.entry(
            "T2", source="原话 2",
            artifact=f"在跑：worker（pid {worker_pid}）；下次核：{later}；产物路径",
        )
        entries += self.entry("T3", source="原话 3", artifact=f"等：用户：确认参数；下次核：{later}")
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [
            self.quote(1, "T1"), self.quote(2, "T2"), self.quote(3, "T3"),
        ])

        self.assert_passes()

    def test_wait_list_rejects_a_closed_target_even_when_another_target_is_open(self):
        # 列出的每个目标都必须仍在清单中，不能漏掉后面的关闭目标。
        later = self.later()
        entries = self.entry("T1", artifact=f"等：T2、T9；下次核：{later}")
        entries += self.entry("T2", source="原话 2", artifact=f"等：用户：确认参数；下次核：{later}")
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [self.quote(1, "T1"), self.quote(2, "T2")])
        self.write_ledger(lines=["- T9 已验收（产物路径；验收 reports/accept-T9.md）"])

        output = self.assert_problem("等：T9 指向不在清单中的条目")
        self.assertIn("goal.md:8: ", output)

    def test_cycle_through_second_wait_target_is_reported(self):
        # 每个目标都是图边，第二个目标形成的环也必须被发现。
        later = self.later()
        entries = self.entry("T1", artifact=f"等：T2、T3；下次核：{later}")
        entries += self.entry("T2", source="原话 2", artifact=f"等：用户：确认参数；下次核：{later}")
        entries += self.entry("T3", source="原话 3", artifact=f"等：T1；下次核：{later}")
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [
            self.quote(1, "T1"), self.quote(2, "T2"), self.quote(3, "T3"),
        ])

        output = self.assert_problem("等待链成环")
        self.assertIn("T1", output)
        self.assertIn("T3", output)
        self.assertIn("goal.md:8: ", output)

    def test_waiting_for_an_acceptance_pending_task_is_a_valid_terminal(self):
        # [x] 待验收事项仍在清单中，可以作为等待链的终点。
        later = self.later()
        entries = self.entry("T1", artifact=f"等：T2；下次核：{later}")
        entries += self.entry("T2", status="x", source="原话 2",
                              artifact=f"待验收：reports/accept-T2.md；下次核：{later}")
        path = self.write_contract(entries=entries)
        self.write_quotes(path, [self.quote(1, "T1"), self.quote(2, "T2")])

        self.assert_passes()

    def test_quotes_live_in_a_separate_file(self):
        later = self.later()
        path = self.write_contract(entries=self.entry(artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, [self.quote()])
        self.assert_passes()

        self.clear_contracts()
        self.write_contract(entries=self.entry(artifact=f"等：用户：等待输入；下次核：{later}"))
        output = self.assert_problem("缺少原话文件 goal.quotes.md")
        self.assertIn("goal.md:1: ", output)

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(artifact=f"等：用户：等待输入；下次核：{later}"))
        path.write_text(path.read_text(encoding="utf-8") + "## 用户原话\n（原话 1 → T3）x\n",
                        encoding="utf-8")
        self.write_quotes(path, [self.quote()])
        self.assert_problem("原话应写在 goal.quotes.md，清单文件里不能有「## 用户原话」")

    def test_quotes_file_starts_with_an_annotation_and_numbers_run_from_one(self):
        later = self.later()
        path = self.write_contract(entries=self.entry(artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, ["（原话 1 → T3）第一条\n\n第一条的第二段"])
        self.assert_passes()

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, ["前言", "（原话 1 → T3）第一条"])
        output = self.assert_problem("原话文件第一个非空行必须是「（原话 N → 落点）」")
        self.assertIn("goal.quotes.md:1: ", output)

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(
            source="原话 2", artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, ["（原话 2 → T3）第一条"])
        self.assert_problem("原话编号应为 1，实际是 2")

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, ["（原话 1 → T3）a", "（原话 3 → 无：问题）b"])
        self.assert_problem("原话编号应为 2，实际是 3")

    def test_source_must_cite_an_existing_quote(self):
        later = self.later()
        path = self.write_contract(entries=self.entry(
            source="来源：spec.md", artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, [self.quote(1, "无：问题")])
        output = self.assert_problem("出处至少含一个原话编号")
        self.assertIn("goal.md:7: ", output)

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(
            source="原话 1、2", artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, [self.quote(1, "T3")])
        self.assert_problem("出处引用的原话 2 不存在")

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(
            source="原话 1、2", artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, [self.quote(1, "T3"), self.quote(2, "T3")])
        self.assert_passes()

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(
            source="原话 1，2", artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, [self.quote(1, "T3"), self.quote(2, "无：问题")])
        output = self.assert_problem("出处里多个原话编号只认「、」")
        self.assertIn("goal.md:7: ", output)

    def test_indented_annotation_inside_a_quote_is_body_text(self):
        # 标注只认顶格：用户贴进来的「（原话 N → …）」缩进一格后就是正文，不是新的标注。
        path = self.write_contract(entries=self.entry(
            artifact=f"等：用户：等待输入；下次核：{self.later()}"))
        self.write_quotes(path, ["（原话 1 → T3）用户贴了契约片段：\n （原话 1 → T404）片段里的标注"])
        self.assert_passes()

    def test_original_quote_requires_a_landing_but_accepts_rule_and_none_landings(self):
        # 原话标注必须说明落到条目、规矩或无落点。
        path = self.write_contract(entries=[])
        self.write_quotes(path, ["（原话 1 → ）用户要求。"])
        self.assert_problem("原话 1 缺少落点")

        for landing in ("规矩：记忆 foo", "无：状态查询已回答"):
            with self.subTest(landing=landing):
                self.clear_contracts()
                path = self.write_contract("valid.md", entries=[])
                self.write_quotes(path, [self.quote(1, landing)])
                self.assert_passes()

    def test_landing_to_active_item_must_match_its_source_but_ledger_items_are_allowed(self):
        # 在办条目的原话落点须与出处对应；已入账条目不必留在清单。
        path = self.write_contract(entries=self.entry(
            source="原话 1", artifact=f"等：用户：等待输入；下次核：{self.later()}"))
        self.write_quotes(path, [self.quote(1, "T3"), self.quote(2, "T3")])
        self.assert_problem("原话 2 落到 T3，但 T3 的出处没有原话 2")

        self.clear_contracts()
        path = self.write_contract("in-ledger.md", entries=[])
        self.write_quotes(path, [self.quote(1, "T2")])
        self.write_ledger("in-ledger.done.md", lines=[
            "- T2 已验收目标（产物路径；验收 reports/accept-T2.md）",
        ])
        self.assert_passes()

        self.clear_contracts()
        path = self.write_contract("cancelled-ledger.md", entries=[])
        self.write_quotes(path, [self.quote(1, "T2"), self.quote(2, "T3")])
        self.write_ledger("cancelled-ledger.done.md", lines=[
            "- T2 已验收目标（产物路径；验收 reports/accept-T2.md）",
            "- [~] T3 用户取消（原话 2：「不做」）",
        ])
        self.assert_passes()

        self.clear_contracts()
        path = self.write_contract("unknown.md", entries=[])
        self.write_quotes(path, [self.quote(1, "T404")])
        self.write_ledger("unknown.done.md", lines=[
            "- T5 已验收其他目标（产物路径；验收 reports/accept-T5.md）",
        ])
        output = self.assert_problem("原话 1 的落点 T404 不在清单也不在账本")
        self.assertIn("unknown.quotes.md:1: ", output)

    def test_landing_categories_require_descriptions_and_do_not_extract_ids_from_them(self):
        # 分号分隔不同落点；规矩与无的说明须非空，其中的 T 编号不算条目落点。
        for landing in ("规矩：", "无："):
            with self.subTest(landing=landing):
                self.clear_contracts()
                path = self.write_contract(entries=[])
                self.write_quotes(path, [self.quote(1, landing)])
                expected = f"{landing[:3]}后的说明不能为空"
                self.assert_problem(expected)

        later = self.later()
        self.clear_contracts()
        path = self.write_contract(entries=self.entry(
            source="原话 2", artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, [self.quote(1, "无：T3 已关闭"), self.quote(2, "T3")])
        self.assert_passes()

        self.clear_contracts()
        path = self.write_contract(entries=self.entry(
            source="原话 1", artifact=f"等：用户：等待输入；下次核：{later}"))
        self.write_quotes(path, [self.quote(1, "T3；规矩：记忆 foo；无：已答")])
        self.assert_passes()

    def test_required_template_headers_cron_and_session(self):
        # 契约头和清单区段都存在。
        path = self.write_contract(entries=[])
        self.write_quotes(path, [])
        self.assert_passes()

        self.clear_contracts()
        path = self.write_contract("no-list.md", entries=[], checklist_header=False)
        self.write_quotes(path, [])
        self.assert_problem("缺少「## 清单」标题")

        self.clear_contracts()
        path = self.write_contract("no-cron.md", entries=[], cron=False)
        self.write_quotes(path, [])
        self.assert_problem("缺少 cron_job_id: 行")

        self.clear_contracts()
        path = self.write_contract("no-session.md", entries=[], session=None)
        self.write_quotes(path, [])
        self.assert_problem("缺少 session: 行")

        self.clear_contracts()
        path = self.write_contract("empty-cron.md", entries=[], cron_value="")
        self.write_quotes(path, [])
        output = self.assert_problem("cron_job_id: 值不能为空")
        self.assertIn("empty-cron.md:2: ", output)
        self.assertEqual(len(output.splitlines()), 1, output)

    def test_each_session_has_one_contract_but_distinct_sessions_are_allowed(self):
        # 一个 session 只允许一份契约。
        self.write_quotes(self.write_contract("a.md", entries=[], session="shared"), [])
        self.write_quotes(self.write_contract("b.md", entries=[], session="shared"), [])
        output = self.assert_problem(
            "session shared 已有另一份契约 b.md，一个会话只许一份"
        )
        self.assertIn("a.md:1: ", output)
        self.assertIn("session shared 已有另一份契约 a.md，一个会话只许一份", output)
        self.assertIn("b.md:1: ", output)
        self.assertEqual(len(output.splitlines()), 2, output)

        self.clear_contracts()
        self.write_quotes(self.write_contract("a.md", entries=[], session="one"), [])
        self.write_quotes(self.write_contract("b.md", entries=[], session="two"), [])
        output = self.assert_passes().stdout
        self.assertIn("session=one", output)
        self.assertIn("session=two", output)

    def test_original_text_mention_and_multiline_criteria_are_not_misparsed(self):
        # 原话正文里的相似字样不是段首标注，跨行判据照样属于同一条目。
        rows = self.entry(artifact=f"等：用户：等待输入；下次核：{self.later()}")
        rows.insert(2, "    判据第二行")
        path = self.write_contract(entries=rows)
        self.write_quotes(path, [
            self.quote(1, "T3"),
            "正文里提到「（原话 3 说过旧口径）」但这不是标注。",
        ])
        self.assert_passes()

    def test_item_of_exactly_300_chars_passes_and_301_fails(self):
        # 用户要求的字数线：每项从标题行到产物行（含行间换行）恰好 300 字通过，多一字报错，报在标题行。
        path = self.write_contract(entries=self.entry_of_length(300))
        self.write_quotes(path, [self.quote(1, "T3")])
        self.assert_passes()

        self.clear_contracts()
        path = self.write_contract(entries=self.entry_of_length(301))
        self.write_quotes(path, [self.quote(1, "T3")])
        output = self.assert_problem("条目 T3 四行合计 301 字，超过 300 字")
        self.assertIn("goal.md:5: ", output)

    def test_criteria_continuation_lines_count_toward_the_item_limit(self):
        # 判据的续行也算在这一项里：290 字的条目加一行 24 字的续行（含换行 25 字）就超线。
        rows = self.entry_of_length(290)
        rows.insert(2, "    " + "续" * 20)
        path = self.write_contract(entries=rows)
        self.write_quotes(path, [self.quote(1, "T3")])
        self.assert_problem("条目 T3 四行合计 315 字，超过 300 字")

    def test_checklist_file_of_exactly_5000_chars_passes_and_5001_fails(self):
        # 用户要求的字数线：清单文件全文（含开头三行和每个换行）恰好 5000 字通过，多一字报错，报在第 1 行。
        path = self.write_contract(entries=[])
        self.write_quotes(path, [])
        text = path.read_text(encoding="utf-8")
        padded = text.replace("# 测试目标", "# 测试目标" + "长" * (5000 - len(text)), 1)
        path.write_text(padded, encoding="utf-8")
        self.assert_passes()

        path.write_text(padded.replace("# 测试目标", "# 测试目标长", 1), encoding="utf-8")
        output = self.assert_problem("清单合计 5001 字，超过 5000 字")
        self.assertIn("goal.md:1: ", output)

    def test_quotes_and_ledger_do_not_count_toward_the_limits(self):
        # 字数线只管清单文件：原话和账本再长也不触发。
        path = self.write_contract(entries=[])
        self.write_quotes(path, ["（原话 1 → T1）" + "长" * 6000])
        self.write_ledger(lines=["- T1 " + "长" * 6000 + "（产物路径；验收 通过）"])
        self.assert_passes()

    def test_done_ledgers_require_one_of_the_two_closed_item_forms(self):
        # 账本只接纳验收记录与有原话依据的取消记录。
        path = self.write_contract(entries=[])
        self.write_quotes(path, [self.quote(1, "T2")])
        self.write_ledger(lines=[
            "- T1 已验收目标（产物路径；验收 reports/accept-T1.md）",
            "- [~] T2 用户取消（原话 1：「不做」）",
        ])
        self.assert_passes()

        self.clear_contracts()
        path = self.write_contract(entries=[])
        self.write_quotes(path, [])
        self.write_ledger(lines=["- [ ] T5 还在办（报告路径）"])
        output = self.assert_problem("账本格式错误")
        self.assertIn("goal.done.md:1: ", output)

        self.clear_contracts()
        path = self.write_contract(entries=[])
        self.write_quotes(path, [])
        self.write_ledger(lines=[""])
        self.assert_problem("账本格式错误（不允许空行）")

        self.clear_contracts()
        path = self.write_contract(entries=[])
        self.write_quotes(path, [])
        self.write_ledger(lines=["- T6 一句话（没有验收或原话来源）"])
        self.assert_problem("账本格式错误")

    def test_cancelled_ledger_entry_cites_an_existing_quote(self):
        path = self.write_contract(entries=[])
        self.write_quotes(path, [self.quote(1, "无：问题")])
        self.write_ledger(lines=["- [~] T2 用户取消（原话 9：「不做」）"])
        output = self.assert_problem("取消记录引用的原话 9 不存在")
        self.assertIn("goal.done.md:1: ", output)

        self.clear_contracts()
        path = self.write_contract(entries=[])
        self.write_quotes(path, [self.quote(1, "T2")])
        self.write_ledger(lines=["- [~] T2 用户取消（原话 1：「不做」）",
                                 "- T3 做完的事（产物 a.py；验收 python3 -m unittest 退出 0）"])
        self.assert_passes()

    def test_only_top_level_contracts_are_checked(self):
        # 只读目录顶层的清单文件；子目录里的不读，没有清单的账本也不读。
        path = self.write_contract("active.md", entries=[])
        self.write_quotes(path, [])
        self.write_contract("archive/old.md", entries=[])
        self.write_contract("nested/ignored.md", entries=[])
        self.write_ledger("orphan.done.md", lines=["不是账本记录"])
        result = self.assert_passes()
        self.assertIn("active.md", result.stdout)
        self.assertNotIn("old.md", result.stdout)
        self.assertNotIn("ignored.md", result.stdout)
        self.assertNotIn("orphan.done.md", result.stdout)

    def test_missing_argument_exits_with_usage_code_2(self):
        result = self.run_check([])
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("必须显式提供契约目录", result.stderr)


if __name__ == "__main__":
    unittest.main()
