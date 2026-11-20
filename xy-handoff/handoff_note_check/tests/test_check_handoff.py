import json, subprocess, sys, tempfile, unittest
import os
from pathlib import Path


def handoff():
    text = (Path(__file__).resolve().parents[2] / "SKILL.md").read_text(encoding="utf-8")
    sample = text.split("```json\n", 1)[1].split("\n```", 1)[0]
    return json.loads(sample)


class CheckHandoffTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "交接.json"

    def run_text(self, text):
        self.path.write_text(text, encoding="utf-8")
        return self.run_command(self.path)

    def run_command(self, path):
        return subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "check_handoff.py"), str(path)],
            capture_output=True, text=True, check=False,
        )

    def fake_claude(self):
        directory = self.path.parent / "bin"
        directory.mkdir(exist_ok=True)
        executable = directory / "claude"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "Path = __import__('pathlib').Path\n"
            "Path(os.environ['HANDOFF_LAUNCH_LOG']).write_text(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n"
            "sys.stdout.write('backgrounded · fake · handoff-name\\n')\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        return directory

    def run_start(self, name="handoff-name"):
        directory = self.fake_claude()
        launch_log = self.path.parent / "launch.json"
        start_directory = self.path.parent / "start-here"
        start_directory.mkdir(exist_ok=True)
        environment = os.environ.copy()
        environment["PATH"] = str(directory) + os.pathsep + environment.get("PATH", "")
        environment["HANDOFF_LAUNCH_LOG"] = str(launch_log)
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "check_handoff.py"),
             "start", str(self.path), "--dir", str(start_directory), "--name", name],
            capture_output=True, text=True, check=False, env=environment,
        )
        return result, launch_log, start_directory

    def assert_rejected(self, document, *fields):
        result = self.run_text(json.dumps(document, ensure_ascii=False))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        for field in fields:
            self.assertIn(field, result.stdout)

    def test_valid_child_and_independent_handoffs(self):
        document = handoff()
        document["reply"] = "不要"
        result = self.run_text(json.dumps(document, ensure_ascii=False))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")
        document["reply"] = "也许"
        self.assert_rejected(document, "reply")

    def test_facts_decisions_and_boundaries_may_be_empty(self):
        document = handoff()
        document.update(facts=[], decide_yourself=[], boundaries=[])
        result = self.run_text(json.dumps(document, ensure_ascii=False))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")

    def test_conflicting_duplicate_reply_is_rejected(self):
        text = json.dumps(handoff(), ensure_ascii=False)
        result = self.run_text(text[:-1] + ', "reply": "不要"}')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("reply", result.stdout)
        self.assertIn("重复", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_extra_keys_are_rejected(self):
        for path in ((), ("quotes", 0)):
            with self.subTest(path=path):
                document = handoff()
                target = document
                for key in path:
                    target = target[key]
                target["cleanup"] = "删除本会话"
                self.assert_rejected(document, "cleanup", "多余")

    def test_missing_top_level_keys_are_reported(self):
        self.assert_rejected(
            {}, "from", "task", "reply", "quotes", "rules", "facts",
            "decide_yourself", "boundaries", "缺少",
        )

    def test_empty_required_strings_and_list_items_are_rejected(self):
        paths = (("from", "name"), ("from", "short_id"), ("task", "what"),
                 ("task", "done_when"), ("task", "others"), ("rules",),
                 ("facts", 0), ("decide_yourself", 0), ("boundaries", 0),
                 ("quotes", 0, "text"), ("quotes", 0, "source"))
        for path in paths:
            for value in ("", " \n\t"):
                with self.subTest(path=path, value=value):
                    document = handoff()
                    target = document
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = value
                    self.assert_rejected(document, str(path[0]), "不能为空")

    def test_quotes_must_not_be_empty(self):
        document = handoff()
        document["quotes"] = []
        self.assert_rejected(document, "quotes", "不能为空")

    def test_each_quote_requires_text_and_source(self):
        document = handoff()
        document["quotes"] = [{"text": "第一条"}, {"source": "/quotes/2.md"}]
        self.assert_rejected(document, "quotes[0].source", "quotes[1].text",
                             "缺少字段")

    def test_wrong_json_types_are_rejected(self):
        for key, value in (("from", "派方"), ("quotes", {}),
                           ("quotes", ["原话"]), ("rules", []),
                           ("boundaries", [False])):
            with self.subTest(key=key, value=value):
                document = handoff()
                document[key] = value
                self.assert_rejected(document, key, "必须是")
        self.assert_rejected([], "交接说明", "必须是")

    def test_invalid_json_is_reported_without_a_traceback(self):
        result = self.run_text('{"reply":')
        self.assertEqual(result.returncode, 1)
        self.assertIn("JSON", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_missing_file_is_reported_without_a_traceback(self):
        result = self.run_command(self.path)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(self.path), result.stdout)
        self.assertEqual(result.stderr, "")

    def test_start_validates_then_launches_with_the_fixed_prompt_in_the_given_directory(self):
        self.path.write_text(json.dumps(handoff(), ensure_ascii=False), encoding="utf-8")
        result, launch_log, start_directory = self.run_start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "backgrounded · fake · handoff-name\n")
        self.assertEqual(result.stderr, "")
        launched = json.loads(launch_log.read_text(encoding="utf-8"))
        self.assertEqual(launched["cwd"], str(start_directory))
        self.assertEqual(launched["argv"], [
            "--bg", "-n", "handoff-name",
            f"请先读交接说明 {self.path.resolve()}，然后照做。这件事由你全权负责，结论按交接说明里的回复方式汇报。",
        ])

    def test_start_does_not_call_claude_when_handoff_check_fails(self):
        document = handoff()
        del document["task"]["done_when"]
        self.path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        result, launch_log, _ = self.run_start()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("done_when", result.stdout)
        self.assertFalse(launch_log.exists())
