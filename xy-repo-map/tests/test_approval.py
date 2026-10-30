"""Approval output and hook-quote verification tests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.approval import approval_changes, record_approval


DIGEST = "a" * 64


def model(elements, relations):
    return {"elements": elements, "relations": relations}


def element(element_id, title, description, kind):
    return {"id": element_id, "kind": kind, "title": title,
            "description": {"txt": description}, "style": {}}


def hook_document(question, labels, answer):
    questions = [{"question": question, "options": [{"label": label} for label in labels]}]
    answers = {question: answer}
    return {
        "tool_input": {"questions": questions, "answers": answers},
        "tool_response": {"questions": questions, "answers": dict(answers)},
    }


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.quote = self.root / "run" / "xy-goal" / "quotes" / "39.md"
        self.quote.parent.mkdir(parents=True)
        self.repo_map = self.root / "repo-map"
        self.repo_map.mkdir()
        (self.repo_map / "approvals.txt").write_text("", encoding="utf-8")

    def write_hook(self, document):
        self.quote.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def valid_hook(self, answer="同意改图", labels=None, question=None):
        if labels is None:
            labels = ["同意改图", "不同意"]
        if question is None:
            question = f"请确认 {self.digest_line()}"
        self.write_hook(hook_document(question, labels, answer))

    @staticmethod
    def digest_line():
        return f"repo-map sha256 {DIGEST}"

    def assert_record_rejected(self, document, message):
        self.write_hook(document)
        with self.assertRaisesRegex(ValueError, message):
            record_approval(self.repo_map, self.quote, DIGEST, "2026-10-31")

    def test_first_build_reports_every_node_and_relation_as_added(self):
        current = model({"root": element("root", "根", "一句功能描述", "node")}, {
            "r1": {"id": "r1", "source": {"model": "root"},
                   "target": {"model": "root"}, "title": "自循环"},
        })
        changes = approval_changes(None, current)
        self.assertEqual([item["id"] for item in changes["added_elements"]], ["root"])
        self.assertEqual(changes["added_elements"][0]["description"], "一句功能描述")
        self.assertEqual(changes["added_relationships"], [("root", "root", "自循环")])
        self.assertEqual(changes["deleted_elements"], [])

    def test_approval_diff_lists_added_deleted_and_changed_nodes_and_relations(self):
        previous = model({
            "root": element("root", "根", "一句功能描述", "node"),
            "root.old": element("root.old", "旧功能", "一句功能描述", "node"),
            "root.change": element("root.change", "旧标题", "旧说明", "node"),
        }, {"old": {"source": {"model": "root"}, "target": {"model": "root.old"}, "title": "旧关系"}})
        current = model({
            "root": element("root", "根", "一句功能描述", "node"),
            "root.new": element("root.new", "新功能", "一句功能描述", "node"),
            "root.change": element("root.change", "新标题", "新说明", "repo"),
        }, {"new": {"source": {"model": "root"}, "target": {"model": "root.new"}, "title": "新关系"}})
        changes = approval_changes(previous, current)
        self.assertEqual([item["id"] for item in changes["added_elements"]], ["root.new"])
        self.assertEqual([item["id"] for item in changes["deleted_elements"]], ["root.old"])
        self.assertEqual(changes["changed_elements"][0]["fields"], ["kind", "title", "description"])
        self.assertEqual(changes["added_relationships"], [("root", "root.new", "新关系")])
        self.assertEqual(changes["deleted_relationships"], [("root", "root.old", "旧关系")])

    def test_record_writes_only_after_the_hook_records_this_digest_and_exact_answer(self):
        self.valid_hook()
        self.assertTrue(record_approval(self.repo_map, self.quote, DIGEST, "2026-10-31"))
        self.assertEqual((self.repo_map / "approvals.txt").read_text(encoding="utf-8"),
                         f"repo-map sha256 {DIGEST} 2026-10-31 {self.quote}\n")

    def test_record_rejects_paths_that_are_not_contract_quotes(self):
        wrong_path = self.root / "quotes" / "39.md"
        wrong_path.parent.mkdir()
        wrong_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "原话路径"):
            record_approval(self.repo_map, wrong_path, DIGEST, "2026-10-31")

    def test_record_rejects_missing_response_or_mismatched_answers(self):
        self.assert_record_rejected({"tool_input": {"questions": [], "answers": {}}}, "tool_response")
        document = hook_document(f"{self.digest_line()}?", ["同意改图"], "同意改图")
        document["tool_response"]["answers"][f"{self.digest_line()}?"] = "不同意"
        self.assert_record_rejected(document, "answers 不一致")

    def test_record_rejects_wrong_digest_missing_option_and_wrong_answer(self):
        self.valid_hook(question="请确认 repo-map sha256 " + "b" * 64)
        with self.assertRaisesRegex(ValueError, "摘要不匹配"):
            record_approval(self.repo_map, self.quote, DIGEST, "2026-10-31")

        self.valid_hook(labels=["可以", "不同意"])
        with self.assertRaisesRegex(ValueError, "选项"):
            record_approval(self.repo_map, self.quote, DIGEST, "2026-10-31")

        self.valid_hook(answer="1")
        with self.assertRaisesRegex(ValueError, "回答"):
            record_approval(self.repo_map, self.quote, DIGEST, "2026-10-31")

    def test_record_does_not_duplicate_an_existing_digest(self):
        self.valid_hook()
        self.assertTrue(record_approval(self.repo_map, self.quote, DIGEST, "2026-10-31"))
        before = (self.repo_map / "approvals.txt").read_text(encoding="utf-8")
        self.assertFalse(record_approval(self.repo_map, self.quote, DIGEST, "2026-10-31"))
        self.assertEqual((self.repo_map / "approvals.txt").read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
