import fcntl
import shlex
import shutil

from goal_test_support import (
    GoalTestSupport,
    Path,
    SCRIPT_DIRECTORY,
    StringIO,
    goal,
    json,
    mock,
    os,
    redirect_stderr,
    redirect_stdout,
    subprocess,
    sys,
    tempfile,
)
from goal_common import read_text


class GoalTests(GoalTestSupport):

    def test_read_text_preserves_filesystem_errors(self):
        path = self.root / "unreadable.md"
        caught = []
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError("access denied")):
            try:
                read_text(path)
            except Exception as error:
                caught.append(error)
            else:
                self.fail("读取失败必须抛出原始文件系统异常")
        self.assertEqual(len(caught), 1)
        self.assertIs(type(caught[0]), PermissionError)
        self.assertEqual(str(caught[0]), "access denied")

    def test_open_creates_directory_and_outputs_exact_timer_prompt(self):
        output = self.succeeds("open", "release", "交付新版本")
        contract = self.project / "20261024-093000-release" / "xy-goal"
        self.assertTrue((contract / "goal.md").is_file(), "契约必须写入项目下的 xy-goal 子目录")
        self.assertEqual({p.name for p in contract.iterdir()},
                         {"goal.md", "quotes", "tasks", "done", "links.md"})
        self.assertEqual(list((contract / "tasks").iterdir()), [], "open 不应创建开放事项")
        metadata = (contract / "goal.md").read_text(encoding="utf-8")
        self.assertIn("交付新版本", metadata)
        self.assertIn("session-main", metadata)
        self.assertIn(str(self.pid), metadata)
        self.assertEqual(output, f"本次契约：{contract}。按 xy-goal 的「定时自查」一节做\n")

    def test_open_rejects_symlink_project_without_writing_outside(self):
        outside = self.replace_project_with_symlink()
        before = self.directory_entries(outside)
        self.rejects("open", "release", "交付新版本", message="项目目录")
        self.assertEqual(self.directory_entries(outside), before)
        self.assertTrue(self.project.is_symlink())

    def test_existing_contract_writes_reject_symlink_project_without_writing_outside(self):
        original_root = self.root
        for operation in ("add", "update", "dismiss", "accept", "cancel", "close", "adopt",
                          "hook-prompt", "hook-answer"):
            with self.subTest(operation=operation):
                self.root = original_root / operation
                self.root.mkdir()
                contract = self.open_goal("release")
                self.quote(contract, 1, "发布要求")
                self.add_task(1)
                review = None
                if operation == "close":
                    self.succeeds("accept", "T1", "--verdict", "通过")
                    review = self.close_review(contract)
                outside = self.replace_project_with_symlink()
                before = self.directory_entries(outside)
                arguments = {
                    "add": ("add", "--title", "另一项", "--criteria", "通过",
                            "--source-kind", "原话直提", "--source", "1",
                            "--state", "在办", "--pointer", "无"),
                    "update": ("update", "T1", "--title", "不能越界"),
                    "dismiss": ("dismiss", "1", "--reason", "已答"),
                    "accept": ("accept", "T1", "--verdict", "通过"),
                    "cancel": ("cancel", "T1", "--quote", "1"),
                    "close": ("close", "--review", str(review)),
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

    def test_open_and_adopt_require_exactly_one_session_record_before_writing(self):
        original_root = self.root
        for command in ("open", "adopt"):
            with self.subTest(command=command):
                self.root = original_root / command
                self.root.mkdir()
                self.session = "session-main"
                if command == "open":
                    record = self.projects / self.project_name / f"{self.session}.jsonl"
                    record.unlink()
                    arguments = ("open", "release", "交付新版本")
                else:
                    arguments, contract, _ = self.owner_command_with_inbox("adopt")
                    record = self.projects / self.project_name / f"{self.session}.jsonl"
                    record.unlink()
                for matches in (0, 2):
                    with self.subTest(command=command, matches=matches):
                        if matches == 2:
                            self.session_record(self.session, self.project_name)
                            self.session_record(self.session, "-another-project")
                        before = list(self.root.rglob("*"))
                        self.rejects(*arguments, message="会话记录")
                        self.assertEqual(list(self.root.rglob("*")), before)
                if command == "open":
                    extra_record = self.projects / "-another-project" / f"{self.session}.jsonl"
                    extra_record.unlink(missing_ok=True)

    def test_open_treats_session_id_glob_characters_as_literal(self):
        self.session = "session*id"
        self.session_record(self.session, "-literal-project")
        self.session_record("session-generated-id", "-glob-project")
        self.succeeds("open", "literal-session", "按会话 id 选项目")
        contract = (self.root / "-literal-project" /
                    f"{self.now:%Y%m%d-%H%M%S}-literal-session" / "xy-goal")
        self.assertTrue((contract / "goal.md").is_file())
        self.assertEqual(list((self.root / "-glob-project").glob("*/xy-goal")), [])

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

    def test_existing_contract_commands_and_hooks_do_not_need_session_record(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "两个事项")
        self.quote(contract, 2, "问题已答")
        self.add_task(1)
        self.add_task(1)
        shutil.rmtree(self.projects)
        self.succeeds("adopt", str(contract))
        self.succeeds("show")
        self.succeeds("update", "T1", "--title", "更新后")
        self.succeeds("dismiss", "2", "--reason", "已答")
        self.succeeds("check")
        code, output, errors = self.run_goal(
            "hook-prompt", session=None, pid=None,
            stdin=json.dumps({"session_id": self.session, "prompt": "取消第二项"}),
        )
        self.assertEqual((code, output, errors), (0, "", ""))
        self.succeeds("accept", "T1", "--verdict", "通过")
        self.succeeds("cancel", "T2", "--quote", "3")
        self.close_contract(contract)
        self.assertFalse((contract / "goal.md").exists())

    def test_trash_contract_is_neither_found_by_hook_nor_adopted(self):
        contract = self.open_goal("release")
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

    def test_close_returns_hooks_to_inbox_and_allows_a_new_contract(self):
        contract = self.open_goal("release")
        self.quote(contract, 1, "第一件事")
        self.succeeds("dismiss", "1", "--reason", "已答")
        self.close_contract(contract)
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

    def test_missing_or_non_directory_root_is_refused_without_writes(self):
        original_root = self.root
        commands = (
            (("hook-prompt",), {"session_id": self.session, "prompt": "不能丢失的原话"}),
            (("open", "release", "交付新版本", "--claude-pid", str(self.pid)), None),
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
                    if arguments[0] == "hook-prompt":
                        self.assert_hook_error((code, output, errors), arguments[0],
                                               message=str(self.root), record="这次输入没有记入契约。")
                    else:
                        self.assertEqual(code, 1, output + errors)
                        self.assertEqual(output, "")
                        self.assertIn(str(self.root), errors)

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

    def test_open_refuses_an_existing_contract(self):
        self.open_goal("release")
        self.rejects("open", "other", "另一个目标", message="已有契约")

    def test_duplicate_session_contracts_are_rejected(self):
        first = self.open_goal("release")
        shutil.copytree(first, self.project / "20261024-093001-copy" / "xy-goal")
        self.rejects("show", message="多份契约")

    def test_foreign_invalid_or_unreadable_metadata_does_not_block_own_contract(self):
        original_root = self.root
        for metadata_kind in ("invalid-utf8", "unreadable"):
            with self.subTest(metadata_kind=metadata_kind):
                self.root = original_root / metadata_kind
                self.root.mkdir()
                self.session = "session-main"
                contract = self.open_goal("release")
                foreign = self.project / "foreign-contract" / "xy-goal"
                foreign.mkdir(parents=True)
                metadata = foreign / "goal.md"
                if metadata_kind == "invalid-utf8":
                    metadata.write_bytes(b'target: "\xff"\nsession_id: "foreign-session"\nclaude_pid: "123"\n')
                    original = metadata.read_bytes()
                    self.succeeds("check")
                    self.hook("hook-prompt", {"session_id": self.session, "prompt": "本会话新要求"})
                    self.hook("hook-answer", {"session_id": self.session, "tool_name": "AskUserQuestion",
                                               "tool_input": {"question": "继续？"}, "tool_response": "继续"})
                    self.assertEqual((contract / "quotes" / "1.md").read_text(), "本会话新要求")
                    self.assertEqual(json.loads((contract / "quotes" / "2.md").read_text())["tool_response"], "继续")
                    self.assertEqual(metadata.read_bytes(), original)
                else:
                    metadata.write_text('session_id: "foreign-session"\n')
                    metadata.chmod(0)
                    try:
                        self.succeeds("check")
                        self.hook("hook-prompt", {"session_id": self.session, "prompt": "仍能记录"})
                        self.assertEqual((contract / "quotes" / "1.md").read_text(), "仍能记录")
                    finally:
                        metadata.chmod(0o600)

    def test_own_invalid_utf8_metadata_is_still_reported(self):
        contract = self.open_goal("release")
        metadata = contract / "goal.md"
        metadata.write_bytes(b'target: "\xff"\nsession_id: "session-main"\nclaude_pid: "123"\n')
        self.rejects("check", message=str(metadata))

    def test_hook_with_empty_quotes_can_be_repaired_without_claiming_dangling_number(self):
        contract = self.open_goal("release")
        (contract / "tasks" / "T2.md").write_text(
            'title: "手写事项"\ncriteria: "完成要求"\nstate: "在办"\npointer: "无"\n')
        (contract / "links.md").write_text("1 T2\n")
        before = self.files()
        result = self.run_goal("hook-prompt", session=None, pid=None,
                               stdin=json.dumps({"session_id": self.session, "prompt": "本次输入"}))
        quote = contract / "quotes" / "2.md"
        self.assert_hook_error(result, "hook-prompt", message="原话 1 不存在",
                               record=f"这次输入已记为原话 2（{quote}）。")
        before[str(quote.relative_to(self.root))] = "本次输入".encode("utf-8")
        self.assertEqual(self.files(), before)
        self.succeeds("relink", "T2", "--source", "2")
        self.assertFalse((contract / "quotes" / "1.md").exists())
        self.assertEqual((contract / "links.md").read_text(), "2 T2\n")
        self.succeeds("check")

    def test_closing_task_without_final_lf_preserves_fields_and_keeps_hook_working(self):
        original_root = self.root
        for operation in ("accept", "cancel"):
            with self.subTest(operation=operation):
                self.root = original_root / operation
                self.root.mkdir()
                contract = self.open_goal("release")
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
                self.assertEqual(goal.Contract(contract, (contract / "goal.md").read_bytes()).done["T1"], {
                    "title": "发布", "criteria": "测试通过",
                    **conclusion,
                })
                self.assertFalse(task.exists())
                self.hook("hook-prompt", {"session_id": self.session, "prompt": "后续要求"})
                self.assertEqual((contract / "quotes" / "2.md").read_text(), "后续要求")
                self.assertIn("还没处理的原话：2", self.succeeds("show"))

    def test_close_refuses_open_tasks_and_unhandled_quotes_then_removes_only_goal_file(self):
        contract = self.open_goal("release")
        other = self.root / "untouched.txt"
        other.write_text("别的文件")
        self.quote(contract, 1, "发布")
        report = self.close_review(contract)
        self.rejects("close", "--review", str(report), message="还没处理")
        self.add_task(1)
        report = self.close_review(contract)
        self.rejects("close", "--review", str(report), message="开放事项")
        self.succeeds("accept", "T1", "--verdict", "通过")
        before = self.files()
        self.close_contract(contract)
        del before[str((contract / "goal.md").relative_to(self.root))]
        self.assertEqual(self.files(), before)
        self.assertTrue(contract.is_dir())
        self.assertEqual(other.read_text(), "别的文件")

    def test_close_requires_a_current_independent_review_report(self):
        contract = self.open_goal("release")
        self.rejects_argument_error("close", message="--review")

        report = self.close_review(contract, reviewer_session=self.session)
        self.rejects("close", "--review", str(report), message="复核者会话")

        report = self.close_review(contract, contract_directory=str(self.root / "another" / "xy-goal"))
        self.rejects("close", "--review", str(report), message="契约目录")

        report = self.close_review(contract, verdict="不通过")
        self.rejects("close", "--review", str(report), message="复核结论")

        report = self.close_review(contract)
        metadata = contract / "goal.md"
        metadata.write_text(metadata.read_text(encoding="utf-8").replace("交付新版本", "修改后的目标"),
                            encoding="utf-8")
        self.rejects("close", "--review", str(report), message="指纹")

    def test_fingerprint_covers_contract_target_and_is_read_only(self):
        contract = self.open_goal("release")
        before = self.files()
        original = self.cli_command("fingerprint", str(contract))
        self.assertEqual(original.returncode, 0, original.stdout + original.stderr)
        self.assertRegex(original.stdout, r"^[0-9a-f]{64}\n$")
        self.assertEqual(self.files(), before)
        metadata = contract / "goal.md"
        metadata.write_text(metadata.read_text(encoding="utf-8").replace("交付新版本", "修改后的目标"),
                            encoding="utf-8")
        changed = self.cli_command("fingerprint", str(contract))
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        self.assertNotEqual(original.stdout, changed.stdout)

    def test_unicode_line_separators_round_trip_in_native_fields(self):
        original_root = self.root
        for separator in ("\u2028", "\u2029", "\u0085"):
            for operation in ("add", "update", "accept"):
                with self.subTest(separator=repr(separator), operation=operation):
                    self.root = original_root / f"{ord(separator)}-{operation}"
                    self.root.mkdir()
                    contract = self.open_goal("release")
                    self.quote(contract, 1, "按要求完成")
                    text = "甲" + separator + "乙"
                    if operation == "add":
                        self.succeeds("add", "--title", text, "--criteria", "通过",
                                      "--source-kind", "原话直提", "--source", "1",
                                      "--state", "在办", "--pointer", "无")
                    else:
                        self.add_task(1)
                        if operation == "update":
                            self.succeeds("update", "T1", "--pointer", text)
                        else:
                            self.succeeds("accept", "T1", "--verdict", text)
                    self.succeeds("check")
                    saved = goal.Contract(contract, (contract / "goal.md").read_bytes())
                    if operation == "accept":
                        self.assertEqual(saved.done["T1"]["verdict"], text)
                    else:
                        self.assertEqual(saved.tasks["T1"]["title" if operation == "add" else "pointer"], text)
                        self.assertEqual(len(self.succeeds("show").splitlines()), 6)

    def test_required_arguments_and_empty_fields_are_rejected_without_writes(self):
        self.rejects_argument_error("open", "release", message="target")
        self.rejects("open", "../escape", "目标", message="slug")
        self.rejects("open", "release", "", message="目标")
        contract = self.open_goal("release")
        self.quote(contract, 1, "发布")
        for field in ("title", "criteria", "state", "pointer", "source", "source-kind"):
            values = {"title": "发布", "criteria": "通过", "state": "在办", "pointer": "无",
                      "source": "1", "source-kind": "原话直提"}
            arguments = ["add"]
            for name, value in values.items():
                if name != field:
                    arguments.extend((f"--{name}", value))
            with self.subTest(field=field):
                self.rejects_argument_error(*arguments, message=f"--{field}")
        self.rejects("add", "--title", "", "--criteria", "通过", "--state", "在办",
                     "--pointer", "无", "--source", "1", "--source-kind", "原话直提",
                     message="标题")

    def test_non_hook_commands_still_raise_unexpected_exceptions(self):
        before = self.files()
        with mock.patch("goal.find_contract", side_effect=RuntimeError("模拟定位意外失败")):
            with self.assertRaisesRegex(RuntimeError, "模拟定位意外失败"):
                self.command("show")
        self.assertEqual(self.files(), before)

    def test_argument_errors_use_standard_argparse_exit_without_writes(self):
        missing_fields = "the following arguments are required: --title, --criteria, --state, --pointer, --source, --source-kind"
        cases = [(["show"], "the following arguments are required: --root")]
        for command in ("hook-prompt", "hook-answer"):
            cases.extend((
                ([command], "the following arguments are required: --root"),
                (["--root", str(self.root), command, "--unexpected"],
                 "unrecognized arguments: --unexpected"),
                (["--root", "--bad", command], "argument --root: expected one argument"),
                (["--root", "--root", command, "show"], "argument --root: expected one argument"),
                (["--root", command, "add"], missing_fields),
                (["--roo", command, "add"], missing_fields),
                ([f"--root={command}", "add"], missing_fields),
                (["--root", command], "the following arguments are required: command"),
                ([f"--root={command}"], "the following arguments are required: command"),
            ))
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                before = self.files()
                output, errors = StringIO(), StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    with self.assertRaises(SystemExit) as error:
                        goal.main(arguments, session_id=self.session, now=self.now)
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertIn(expected, errors.getvalue())
                self.assertEqual(self.files(), before)

    def test_cli_errors_preserve_exact_output(self):
        cases = ((("show",), "本会话没有契约\n"),
                 (("update", "hook-prompt", "--source", "1"), "本会话没有契约\n"),
                 (("open", "release", ""), "目标不能为空\n"))
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                before = self.files()
                code, output, errors = self.command(*arguments)
                self.assertEqual((code, output, errors), (1, "", expected))
                self.assertNotIn("请优先修复本会话契约或 hook 问题，再做别的。", errors)
                self.assertEqual(self.files(), before)

        before = self.files()
        result = self.cli_command("unknown")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("invalid choice", result.stderr)
        self.assertEqual(self.files(), before)

    def test_relative_root_is_rejected_without_expanding_user(self):
        for root in ("relative-runs", "~/runs"):
            with self.subTest(root=root):
                output, errors = StringIO(), StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    code = goal.main(["--root", root, "show"],
                                     session_id=self.session, now=self.now)
                self.assertEqual(code, 1, output.getvalue() + errors.getvalue())
                self.assertEqual(output.getvalue(), "")
                self.assertIn("--root 必须是绝对路径", errors.getvalue())

    def test_cli_reports_missing_session(self):
        environment = os.environ.copy()
        environment.pop("CLAUDE_CODE_SESSION_ID", None)
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT_DIRECTORY / "goal.py"),
             "--root", str(self.root), "show"],
            env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("CLAUDE_CODE_SESSION_ID不能为空", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

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

    def test_cli_rejects_missing_or_invalid_bash_ppid(self):
        for arguments in (("open", "release", "目标"),
                          ("adopt", str(self.root / "old-contract"))):
            for value in (None, "", "not-a-pid", "0", "-1"):
                with self.subTest(arguments=arguments, value=value):
                    before = self.files()
                    if value is None:
                        result = self.cli_command(*arguments)
                    else:
                        result = self.cli_command(*arguments, "--claude-pid", value)
                    if value is None or value in ("", "not-a-pid"):
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertIn("--claude-pid", result.stderr)
                    else:
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        for message in ("--claude-pid", "$PPID", "Claude Code", "Bash"):
                            self.assertIn(message, result.stderr)
                    self.assertEqual(self.files(), before)

    def test_committed_owner_commands_warn_on_inbox_delete_failure_without_reimporting(self):
        for operation, prepared in self.owner_commands_with_inbox():
            with self.subTest(operation=operation):
                arguments, contract, inbox = prepared
                with self.readonly_directory(inbox):
                    code, output, errors = self.command(*arguments)
                    self.assertEqual(code, 0, errors)
                    self.assertEqual(output, f"本次契约：{contract}。按 xy-goal 的「定时自查」一节做\n")
                    self.assertIn("警告", errors)
                    self.assertFalse(inbox.exists(), "已领原话不能继续留在可被再次领取的位置")
                    self.assertEqual(len(list((contract / "quotes").iterdir())), 1)
                    self.succeeds("adopt", str(contract))
                    self.assertEqual(len(list((contract / "quotes").iterdir())), 1)
                self.hook("hook-prompt", {"session_id": self.session, "prompt": "后续消息"})
                self.assertEqual(len(list((contract / "quotes").iterdir())), 2)

    def test_failed_input_claim_rolls_back_owner_commands_and_can_be_retried(self):
        for operation, prepared in self.owner_commands_with_inbox():
            with self.subTest(operation=operation):
                arguments, contract, inbox = prepared
                with self.readonly_directory(inbox.parent):
                    self.rejects(*arguments, message="Permission denied")
                self.succeeds(*arguments)
                self.assertFalse(inbox.exists())
                self.assertEqual(len(list((contract / "quotes").iterdir())), 1)
