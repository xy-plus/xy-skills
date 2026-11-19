import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from goal_test_support import (
    GoalTestSupport,
    os,
    shutil,
    subprocess,
)


class ContractTakeoverTests(GoalTestSupport):

    def test_new_session_adopt_claims_inbox_from_its_own_project(self):
        contract = self.open_goal("release")
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

    def test_adopt_rejects_paths_outside_contract_layer(self):
        contract = self.open_goal("release")
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

    def test_open_and_adopt_refuse_non_ancestor_pid(self):
        original_root = self.root
        parent_pid = self.pid
        with self.subTest(command="open"):
            child = self.live_process()
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

        with self.subTest(command="adopt"):
            self.root = original_root / "adopt"
            self.root.mkdir()
            self.session = "session-main"
            self.pid = parent_pid
            contract = self.open_goal("release")
            old = subprocess.Popen([sys.executable, "-c", "pass"])
            old.wait()
            metadata = contract / "goal.md"
            metadata.write_text(metadata.read_text().replace(f'"{self.pid}"', f'"{old.pid}"'))
            self.session = "unrelated-session"
            self.pid = self.live_process().pid
            self.rejects("adopt", str(contract), message="祖先")

    def test_cli_adopt_records_explicit_bash_parent_pid(self):
        contract = self.open_goal("release")
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
        contract = self.open_goal("release")
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
                         f"本次契约：{contract}。按 xy-goal 的「定时自查」一节做\n")
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
        contract = self.open_goal("release")
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
        contract = self.open_goal("release")
        self.quote(contract, 1, "保留原事项和映射")
        self.add_task(1)
        before = self.files()
        result = self.cli_command("adopt", str(contract), "--claude-pid", str(os.getpid()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'claude_pid: "{os.getpid()}"', (contract / "goal.md").read_text())
        for relative, content in before.items():
            if relative != str((contract / "goal.md").relative_to(self.root)):
                self.assertEqual(self.files()[relative], content)

    def test_adopt_write_failure_after_claim_restores_inbox_for_retry(self):
        arguments, contract, inbox = self.owner_command_with_inbox("adopt")
        with self.readonly_directory(contract):
            self.rejects(*arguments, message="Permission denied")
        self.assertEqual((inbox / "1.md").read_text(), "只领取一次")
        self.succeeds(*arguments)
        self.assertFalse(inbox.exists())
        self.assertEqual(len(list((contract / "quotes").iterdir())), 1)

    def test_adopt_refuses_duplicate_contracts_of_the_previous_owner(self):
        contract = self.open_goal("release")
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        metadata = (contract / "goal.md").read_text()
        (contract / "goal.md").write_text(metadata.replace(f'"{self.pid}"', f'"{child.pid}"'))
        shutil.copytree(contract, self.project / "duplicate-old-owner" / "xy-goal")
        self.session = "new-session"
        self.rejects("adopt", str(contract), message="多份契约")
