import json
from pathlib import Path
import unittest

import test_check as support


def legacy_main_report(verdict="通过"):
    report = support.main_report(verdict=verdict)
    report["mapping"]["items"] = [
        {"block": "规则 A", "need": "报告必须能核对用户要求"},
        {"block": "规则 B", "need": "报告必须能核对用户要求"},
    ]
    report["mapping"]["merges"] = [{
        "need": "报告必须能核对用户要求",
        "blocks": ["规则 A", "规则 B"],
        "decision": "不合",
        "reason": "两块分别承担不同职责。",
    }]
    if verdict == "不通过":
        report["hard_to_misuse"] = support.section([{
            "problem": "历史报告记录的缺陷。",
            "evidence": "旧版本报告中的证据。",
            "quote": {"source": "用户原话 1", "text": "照交接说明执行。"},
        }])
    return report


class HistoricalReportChainTest(unittest.TestCase):
    setUp = support.CheckTest.setUp
    run_main = support.CheckTest.run_main
    run_report = support.CheckTest.run_report
    create_formal_round3 = support.CheckTest.create_formal_round3
    create_complete_formal_group = support.CheckTest.create_complete_formal_group

    def test_opening_a_group_uses_legacy_history_without_revalidating_reports_or_derivations(self):
        self.create_complete_formal_group(failed=True, group="group1")
        for number in (1, 2, 3):
            round_path = Path(self.root) / "group1" / f"round{number}"
            report = legacy_main_report("不通过" if number == 3 else "通过")
            support.write_file(round_path, "review-main.json", json.dumps(report, ensure_ascii=False))
            support.write_file(round_path, "derivation-main.json", "{old derivation format")
            support.write_file(round_path, "derivation-razor.json", "{old derivation format")
        support.write_file(self.root, "group1/diagnosis.json", json.dumps(support.diagnosis_report()))

        code, problems = self.run_report(
            "derivation-main.json", support.derivation_report(), ("group2", "round1")
        )

        self.assertEqual((code, problems), (0, []))

    def test_opening_a_group_rejects_history_that_is_not_an_object_or_has_bad_verdict(self):
        self.create_complete_formal_group(failed=True, group="group1")
        support.write_file(self.root, "group1/diagnosis.json", json.dumps(support.diagnosis_report()))
        report_path = Path(self.root) / "group1" / "round1" / "review-main.json"
        invalid_reports = [
            ("[]", "JSON 对象"),
            ("{}", "verdict"),
            (json.dumps({"verdict": "完成"}), "verdict"),
        ]

        for text, expected in invalid_reports:
            with self.subTest(report=text):
                support.write_file(report_path.parent, report_path.name, text)
                code, problems = self.run_report(
                    "derivation-main.json", support.derivation_report(), ("group2", "round1")
                )
                self.assertEqual(code, 1, problems)
                self.assertTrue(
                    any("review-main.json" in problem and expected in problem for problem in problems),
                    problems,
                )
