import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_check as support

check = support.check
derivation_report = support.derivation_report
main_report = support.main_report
write_file = support.write_file


class DerivationReportTest(unittest.TestCase):
    setUp = support.CheckTest.setUp
    run_main = support.CheckTest.run_main
    run_report = support.CheckTest.run_report
    assert_rejected = support.CheckTest.assert_rejected
    make_code_submission = support.CheckTest.make_code_submission
    clean_code_razor_report = support.CheckTest.clean_code_razor_report
    run_code_razor = support.CheckTest.run_code_razor

    def test_code_razor_report_is_decoded_once(self):
        _repository, _base, scan_document = self.make_code_submission()
        report = self.clean_code_razor_report(scan_document)

        with patch.object(
            check.json, "loads", wraps=check.json.loads,
        ) as decode:
            code, output = self.run_code_razor(report, scan_document)

        self.assertEqual((code, output), (0, []))
        self.assertEqual(decode.call_count, 3)

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
