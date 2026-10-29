"""交接 JSON 的命令行测试。运行：在本目录 python3 -B -m unittest。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def handoff():
    return {
        "kind": "普通",
        "from": {"name": "行情重建", "short_id": "a1b2c3d4"},
        "task": {"what": "数据服务", "done_when": "导入样例验收通过", "others": "引擎由引擎会话负责"},
        "reply": "要",
        "quotes": [{"text": "把行情数据接过来。", "source": "/runs/xy-goal/quotes/1.md"}],
        "rules": "无",
        "facts": ["现有数据目录为空"],
        "decide_yourself": ["重试策略"],
        "boundaries": ["保留现有回测界面"],
    }


def cleanup_handoff(contract):
    return {
        "kind": "清扫",
        "from": {"name": "清扫派方", "short_id": "a1b2c3d4"},
        "reply": "要",
        "contract": str(contract),
        "changed": [{"path": "/repo/deleted.py", "in_use_by": "无"}],
    }


class CheckHandoffTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "交接.json"
        self.contract = Path(directory.name) / "契约"
        (self.contract / "quotes").mkdir(parents=True)
        (self.contract / "quotes" / "1.md").write_text("原话\n", encoding="utf-8")

    def run_check(self, document):
        return self.run_text(json.dumps(document, ensure_ascii=False))

    def run_text(self, text):
        self.path.write_text(text, encoding="utf-8")
        before = self.path.read_bytes()
        result = self.run_command(self.path)
        self.assertEqual(self.path.read_bytes(), before, "校验不能修改交接文件")
        return result

    def run_command(self, path):
        return subprocess.run(
            [sys.executable, "-B", str(Path(__file__).with_name("check_handoff.py")), str(path)],
            capture_output=True, text=True, check=False,
        )

    def assert_rejected(self, document, *fields):
        result = self.run_check(document)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        for field in fields:
            self.assertIn(field, result.stdout)

    def test_valid_child_and_independent_handoffs(self):
        for reply in ("要", "不要"):
            with self.subTest(reply=reply):
                document = handoff()
                document["reply"] = reply
                result = self.run_check(document)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout + result.stderr, "")

    def test_valid_cleanup_handoff_and_remote_changed_path(self):
        document = cleanup_handoff(self.contract)
        document["changed"].append({
            "path": "用户@主机:/项目/scripts/active.py",
            "in_use_by": "总仓位RL-ETH（f0a9ec51）",
        })
        result = self.run_check(document)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")

    def test_cleanup_changed_path_need_not_exist(self):
        result = self.run_check(cleanup_handoff(self.contract))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_kind_is_required_and_limits_the_structure(self):
        document = handoff()
        del document["kind"]
        self.assert_rejected(document, "缺少字段：交接说明.kind")

        document = handoff()
        document["kind"] = "清扫任务"
        result = self.run_check(document)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "kind 只能是「普通」或「清扫」\n")

    def test_cleanup_handoff_has_only_its_five_fields(self):
        for field in ("task", "quotes", "rules", "facts", "decide_yourself", "boundaries"):
            with self.subTest(field=field):
                document = cleanup_handoff(self.contract)
                document[field] = []
                self.assert_rejected(document, f"多余字段：{field}")

    def test_cleanup_handoff_requires_contract_and_changed(self):
        for field in ("contract", "changed"):
            with self.subTest(field=field):
                document = cleanup_handoff(self.contract)
                del document[field]
                self.assert_rejected(document, f"缺少字段：交接说明.{field}")

    def test_cleanup_reply_must_be_yes(self):
        document = cleanup_handoff(self.contract)
        document["reply"] = "不要"
        self.assert_rejected(document, "reply 只能是「要」")

    def test_cleanup_changed_must_not_be_empty(self):
        document = cleanup_handoff(self.contract)
        document["changed"] = []
        self.assert_rejected(document, "changed 不能为空")

    def test_cleanup_changed_paths_must_be_absolute(self):
        for path in ("repo/file.py", "用户@主机:项目/file.py"):
            with self.subTest(path=path):
                document = cleanup_handoff(self.contract)
                document["changed"][0]["path"] = path
                self.assert_rejected(document, "changed[0].path", "绝对路径")

    def test_cleanup_in_use_by_must_not_be_blank(self):
        document = cleanup_handoff(self.contract)
        document["changed"][0]["in_use_by"] = " \n\t"
        self.assert_rejected(document, "changed[0].in_use_by", "不能为空")

    def test_cleanup_contract_must_exist_and_contain_a_numbered_quote(self):
        cases = (
            ("missing", "contract 必须是已存在的目录"),
            ("no_quotes", "contract 目录下缺少 quotes/"),
            ("empty_quotes", "quotes/ 里至少要有一个内容非空的 <N>.md"),
        )
        for case, message in cases:
            with self.subTest(case=case):
                document = cleanup_handoff(self.contract)
                if case == "missing":
                    document["contract"] = str(self.contract / "missing")
                elif case == "no_quotes":
                    empty_contract = self.contract / "无原话目录"
                    empty_contract.mkdir()
                    document["contract"] = str(empty_contract)
                else:
                    (self.contract / "quotes" / "1.md").unlink()
                    (self.contract / "quotes" / "readme.md").write_text("说明\n", encoding="utf-8")
                    (self.contract / "quotes" / "0.md").write_text("编号不合法\n", encoding="utf-8")
                self.assert_rejected(document, message)

    def test_cleanup_contract_rejects_only_empty_numbered_quote(self):
        (self.contract / "quotes" / "1.md").write_text(" \n\t", encoding="utf-8")
        self.assert_rejected(
            cleanup_handoff(self.contract),
            "quotes/ 里至少要有一个内容非空的 <N>.md",
        )

    def test_cleanup_contract_accepts_a_nonempty_numbered_quote(self):
        (self.contract / "quotes" / "1.md").write_text(" \n\t", encoding="utf-8")
        (self.contract / "quotes" / "2.md").write_text("原话内容\n", encoding="utf-8")
        result = self.run_check(cleanup_handoff(self.contract))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")

    def test_facts_decisions_and_boundaries_may_be_empty(self):
        document = handoff()
        document.update(facts=[], decide_yourself=[], boundaries=[])
        result = self.run_check(document)
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
        for path in ((), ("from",), ("task",), ("quotes", 0)):
            with self.subTest(path=path):
                document = handoff()
                target = document
                for key in path:
                    target = target[key]
                target["cleanup"] = "删除本会话"
                self.assert_rejected(document, "cleanup", "多余")

    def test_missing_top_level_keys_are_reported(self):
        for key in ("kind", "from", "task", "reply", "quotes", "rules", "facts", "decide_yourself", "boundaries"):
            with self.subTest(key=key):
                document = handoff()
                del document[key]
                self.assert_rejected(document, key, "缺少")

    def test_missing_nested_fields_are_reported(self):
        cases = (("from", "name"), ("from", "short_id"), ("task", "what"),
                 ("task", "done_when"), ("task", "others"))
        for parent, key in cases:
            with self.subTest(parent=parent, key=key):
                document = handoff()
                del document[parent][key]
                self.assert_rejected(document, parent, key, "缺少")

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

    def test_reply_must_be_one_of_the_two_values(self):
        for reply in ("", "不需要", "不要：直接向用户汇报", None, True):
            with self.subTest(reply=reply):
                document = handoff()
                document["reply"] = reply
                self.assert_rejected(document, "reply", "要", "不要")

    def test_each_quote_requires_text_and_source(self):
        document = handoff()
        document["quotes"] = [{"text": "第一条"}, {"source": "/quotes/2.md"}, {}]
        self.assert_rejected(document, "quotes[0].source", "quotes[1].text",
                             "quotes[2].text", "quotes[2].source")

    def test_wrong_json_types_are_rejected(self):
        for key, value in (("from", "派方"), ("task", []), ("quotes", {}),
                           ("quotes", ["原话"]), ("rules", []), ("facts", "事实"),
                           ("decide_yourself", None), ("boundaries", [False])):
            with self.subTest(key=key, value=value):
                document = handoff()
                document[key] = value
                self.assert_rejected(document, key, "必须是")
        self.assert_rejected([], "交接说明", "必须是")

    def test_all_problems_are_reported_together(self):
        document = handoff()
        del document["rules"]
        document["cleanup"] = "删除"
        document["reply"] = "也许"
        document["task"]["what"] = ""
        document["quotes"] = [{"text": "原话"}]
        self.assert_rejected(document, "rules", "cleanup", "reply", "task.what", "quotes[0].source")

    def test_invalid_json_is_reported_without_a_traceback(self):
        self.path.write_text('{"reply":', encoding="utf-8")
        result = self.run_command(self.path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("JSON", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_missing_file_is_reported_without_a_traceback(self):
        result = self.run_command(self.path)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(self.path), result.stdout)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
