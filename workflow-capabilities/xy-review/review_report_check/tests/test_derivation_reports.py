import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_check as support

check = support.check
derivation_report = support.derivation_report
main_report = support.main_report
razor_report = support.razor_report
write_file = support.write_file


REPORT_NEED = "报告必须能核对用户要求"


def repeated_need_report(decision="合并", merged_text="写成一条规则。"):
    report = main_report()
    report["mapping"]["items"] = [
        {"block": "规则 A", "need": REPORT_NEED},
        {"block": "规则 B", "need": REPORT_NEED},
    ]
    report["mapping"]["merges"] = [{
        "need": REPORT_NEED,
        "blocks": ["规则 A", "规则 B"],
        "decision": decision,
        "merged_text": merged_text,
        "reason": "合成一条规则。" if decision == "合并" else "两块承担不同职责。",
    }]
    return report


class DerivationReportTest(unittest.TestCase):
    setUp = support.CheckTest.setUp
    run_main = support.CheckTest.run_main
    run_report = support.CheckTest.run_report
    assert_rejected = support.CheckTest.assert_rejected

    def test_main_mapping_accepts_items_linked_to_round_derivation(self):
        report = main_report()

        code, problems = self.run_report("review-main.json", report)

        self.assertEqual((code, problems), (0, []))

    def test_main_mapping_requires_items_and_matching_needs(self):
        report = main_report()
        report["mapping"]["items"] = []
        self.assert_rejected("review-main.json", report, "mapping.items")

        report = main_report()
        report["mapping"]["items"][0]["need"] = "未在推导中的要求"
        self.assert_rejected("review-main.json", report, "mapping.items", "derivation-main.json")

    def test_main_mapping_requires_one_exact_merge_for_each_repeated_need(self):
        report = main_report()
        report["mapping"]["items"] = [
            {"block": "规则 A", "need": REPORT_NEED},
            {"block": "规则 B", "need": REPORT_NEED},
        ]
        self.assert_rejected("review-main.json", report, "mapping.merges")

        report["mapping"]["merges"] = [{
            "need": REPORT_NEED,
            "blocks": ["规则 A", "另一块"],
            "decision": "不合",
            "merged_text": "写成一条按职责区分的规则。",
            "reason": "两块承担不同职责。",
        }]
        self.assert_rejected("review-main.json", report, "mapping.merges", "items")

        report["mapping"]["merges"][0]["blocks"] = ["规则 A", "规则 B"]
        report["mapping"]["merges"].append(dict(report["mapping"]["merges"][0]))
        self.assert_rejected("review-main.json", report, "mapping.merges", "恰有一条")

    def test_main_mapping_rejects_a_repeated_block_even_when_merge_matches(self):
        report = main_report(verdict="不通过")
        report["mapping"]["items"] = [
            {"block": "规则 A", "need": REPORT_NEED},
            {"block": "规则 A", "need": REPORT_NEED},
        ]
        report["mapping"]["merges"] = [{
            "need": REPORT_NEED,
            "blocks": ["规则 A", "规则 A"],
            "decision": "合并",
            "merged_text": "写成一条规则。",
            "reason": "合成一条规则。",
        }]

        self.assert_rejected("review-main.json", report, "mapping.items", "重复")

    def test_main_mapping_requires_nonblank_merged_text(self):
        report = repeated_need_report(decision="不合", merged_text="   ")

        self.assert_rejected("review-main.json", report, "mapping.merges[0].merged_text", "不能为空")
        del report["mapping"]["merges"][0]["merged_text"]
        self.assert_rejected("review-main.json", report, "mapping.merges[0]", "merged_text")

    def test_main_report_parses_same_round_derivation_once(self):
        report = main_report()
        derivation_text = json.dumps(derivation_report(), ensure_ascii=False)
        original_loads = json.loads

        with patch.object(check.json, "loads", wraps=original_loads) as loads:
            code, problems = self.run_report("review-main.json", report)

        self.assertEqual((code, problems), (0, []))
        derivation_calls = [
            call for call in loads.call_args_list
            if call.args and call.args[0] == derivation_text
        ]
        self.assertEqual(len(derivation_calls), 1, loads.call_args_list)

    def test_main_mapping_requires_nonpassing_verdict_for_unmapped_or_merged_needs(self):
        report = main_report()
        report["mapping"]["items"][0]["need"] = "无"
        self.assert_rejected("review-main.json", report, "mapping", "不能判通过")

        report = repeated_need_report()
        self.assert_rejected("review-main.json", report, "mapping", "不能判通过")
        report["verdict"] = "不通过"
        self.assertEqual(self.run_report("review-main.json", report), (0, []))

    def test_razor_user_visible_requires_direct_user_decision_for_changes(self):
        report = razor_report()
        report["occams_razor"]["keep"][0]["user_visible"] = {
            "change": "把 hook 安装进全局 settings",
        }
        self.assert_rejected("review-razor.json", report, "user_visible.decided_by")

        report["occams_razor"]["keep"][0]["user_visible"]["decided_by"] = {
            "source": "R5",
            "text": "你先判断是否应该这么做；如果应该的话就开。",
        }
        self.assert_rejected("review-razor.json", report, "用户直接决定")

        report["occams_razor"]["keep"][0]["user_visible"]["decided_by"]["text"] = (
            "我决定把 hook 安装进全局 settings。"
        )
        self.assertEqual(self.run_report("review-razor.json", report), (0, []))

    def test_razor_user_visible_validates_exact_shape_and_omits_decision_when_unchanged(self):
        report = razor_report()
        self.assertEqual(self.run_report("review-razor.json", report), (0, []))

        report["occams_razor"]["keep"][0]["user_visible"]["decided_by"] = {
            "source": "Q35",
            "text": "我决定照这个方式做。",
        }
        self.assert_rejected("review-razor.json", report, "不改", "decided_by")

        report = razor_report()
        report["occams_razor"]["keep"][0]["user_visible"] = {
            "change": "调整用户规则",
            "decided_by": {"source": "Q35", "text": "   "},
        }
        self.assert_rejected("review-razor.json", report, "decided_by.text")

    def test_report_templates_are_selected_by_their_declared_filenames(self):
        expected = {
            "review-main.json": "主审报告",
            "review-razor.json": "剃刀报告",
            "diagnosis.json": "判因报告",
            "derivation-main.json": "推导报告",
            "derivation-razor.json": "推导报告",
        }

        self.assertEqual(
            {name: check.REPORTS[name][0] for name in expected}, expected
        )

    def test_derivation_report_requires_the_template_fields_and_nonblank_needs(self):
        document = derivation_report()
        del document["root"]
        self.assert_rejected(
            "derivation-main.json", document, "缺少字段：报告.root"
        )

        document = derivation_report(needs=[{
            "need": " ",
            "quote": {"source": "Q1", "text": "按要求审查"},
        }])
        self.assert_rejected(
            "derivation-razor.json", document, "报告.needs[0].need 不能为空"
        )

    def test_derivation_rule_quotes_must_match_the_numbered_rule(self):
        document = derivation_report(needs=[{
            "need": "保留内容要满足约束",
            "quote": {
                "source": "xy-iron-law 第 2 条",
                "text": "不准确的规范引文",
            },
        }])

        self.assert_rejected(
            "derivation-main.json", document,
            "报告.needs[0].quote.text", "必须逐字引用「xy-iron-law 第 2 条」",
        )

    def test_review_report_requires_its_role_derivation_in_the_same_round(self):
        code, problems = self.run_report(
            "review-main.json", main_report(), create_derivation=False
        )

        self.assertEqual(code, 1)
        self.assertTrue(any("derivation-main.json" in problem for problem in problems), problems)

    def test_review_report_rejects_a_malformed_same_round_derivation(self):
        path = Path(self.root) / "group1" / "round1"
        write_file(path, "derivation-razor.json", json.dumps(derivation_report(), ensure_ascii=False))
        write_file(path, "derivation-main.json", '{"root":"","needs":[],"approach":"方案"}')

        code, problems = self.run_report(
            "review-main.json", main_report(), create_derivation=False
        )

        self.assertEqual(code, 1)
        self.assertTrue(any("derivation-main.json" in problem for problem in problems), problems)
        self.assertTrue(any("root 不能为空" in problem for problem in problems), problems)
