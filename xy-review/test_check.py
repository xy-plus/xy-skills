"""xy-review JSON 报告的结构、内容和正式位置测试。"""
import contextlib
import io
import json
import os
import tempfile
import unittest

from check import main


def section(items=None, checked="核对了实现与 spec"):
    return {"items": [] if items is None else items, "checked": checked}


def main_report(verdict="通过", deviations=None, hard_to_misuse=None, repo_rules=None):
    return {
        "first_principles": {"essence": "用户要的是明确且可检查的行为", "deviations": section(deviations)},
        "hard_to_misuse": section(hard_to_misuse),
        "repo_rules": {str(number): section() for number in range(1, 8)} if repo_rules is None else repo_rules,
        "verdict": verdict,
    }


def razor_report(verdict="通过", keep=None, delete=None, ask_user=None, cleanup_handoff="无"):
    return {
        "occams_razor": {
            "keep": [{"entity": "check.py", "reason": "xy-review 的在用入口"}] if keep is None else keep,
            "delete": {"items": [] if delete is None else delete, "checked": "核对了依赖与调用方"},
            "ask_user": {"items": [] if ask_user is None else ask_user, "checked": "核对了行为变化"},
        },
        "cleanup_handoff": cleanup_handoff,
        "verdict": verdict,
    }


def diagnosis_report(diagnosis="问题在于目标被拆成互相矛盾的两种格式。", actions=None):
    return {"diagnosis": diagnosis, "actions": ["重想"] if actions is None else actions}


def json_text(document):
    return json.dumps(document, ensure_ascii=False)


class CheckTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name

    def run_main(self, path_parts, text):
        path = os.path.join(self.root, *path_parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(text)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["check.py", path])
        return code, output.getvalue().splitlines()

    def run_report(self, name, document, folders=("group1", "round1")):
        return self.run_main((*folders, name), json_text(document))

    def create_round1(self, group="group1"):
        os.makedirs(os.path.join(self.root, group, "round1"), exist_ok=True)

    def assert_rejected(self, name, document, *needles, folders=("group1", "round1")):
        code, problems = self.run_report(name, document, folders)
        self.assertEqual(code, 1, problems)
        for needle in needles:
            self.assertTrue(any(needle in problem for problem in problems), f"没报「{needle}」：{problems}")

    def test_clean_json_reports_pass(self):
        self.assertEqual(self.run_report("review-main.json", main_report()), (0, []))
        self.assertEqual(self.run_report("review-razor.json", razor_report()), (0, []))

    def test_main_report_requires_exact_keys_and_types(self):
        cases = []
        document = main_report()
        del document["verdict"]
        cases.append((document, "缺少字段：报告.verdict"))

        document = main_report()
        document["extra"] = "多余"
        cases.append((document, "多余字段：extra"))

        document = main_report()
        document["first_principles"]["deviations"] = {"items": [], "checked": "对照"}
        document["first_principles"]["deviations"]["extra"] = "多余"
        cases.append((document, "first_principles.deviations 多余字段：extra"))

        document = main_report()
        document["hard_to_misuse"]["items"] = "无"
        cases.append((document, "报告.hard_to_misuse.items 必须是数组"))

        document = main_report()
        document["first_principles"]["deviations"]["items"] = [{"problem": "问题"}]
        cases.append((document, "缺少字段：报告.first_principles.deviations.items[0].evidence"))

        document = main_report()
        document["first_principles"]["essence"] = " \n\t"
        cases.append((document, "报告.first_principles.essence 不能为空"))

        document = main_report()
        document["hard_to_misuse"]["checked"] = []
        cases.append((document, "报告.hard_to_misuse.checked 必须是字符串"))

        for document, message in cases:
            with self.subTest(message=message):
                self.assert_rejected("review-main.json", document, message)

    def test_main_report_requires_repository_rules_one_through_seven(self):
        rules = {str(number): section() for number in range(1, 8)}
        del rules["5"]
        rules["8"] = section()
        document = main_report(repo_rules=rules)
        self.assert_rejected("review-main.json", document,
                             "缺少字段：报告.repo_rules.5", "repo_rules 多余字段：8")

    def test_main_verdict_matches_findings(self):
        findings = [{"problem": "增量：漏掉新约束；用户原话 4「格式规范」", "evidence": "check.py:25"}]
        self.assertEqual(self.run_report("review-main.json", main_report(verdict="改了就过", deviations=findings)), (0, []))
        self.assert_rejected("review-main.json", main_report(verdict="通过", deviations=findings), "通过」但有发现")
        self.assert_rejected("review-main.json", main_report(verdict="不通过"), "但没有发现")
        self.assert_rejected("review-main.json", main_report(verdict="未知"), "verdict 只能是")

    def test_razor_report_requires_exact_keys_types_and_nonblank_strings(self):
        cases = []
        document = razor_report()
        del document["cleanup_handoff"]
        cases.append((document, "缺少字段：报告.cleanup_handoff"))

        document = razor_report()
        document["occams_razor"]["delete"]["extra"] = "多余"
        cases.append((document, "occams_razor.delete 多余字段：extra"))

        document = razor_report()
        document["occams_razor"]["keep"] = [{"entity": "check.py", "reason": " "}]
        cases.append((document, "报告.occams_razor.keep[0].reason 不能为空"))

        document = razor_report()
        document["occams_razor"]["ask_user"]["items"] = [{"entity": "文件"}]
        cases.append((document, "缺少字段：报告.occams_razor.ask_user.items[0].now"))

        document = razor_report()
        document["occams_razor"]["delete"]["checked"] = None
        cases.append((document, "报告.occams_razor.delete.checked 必须是字符串"))

        for document, message in cases:
            with self.subTest(message=message):
                self.assert_rejected("review-razor.json", document, message)

    def test_razor_report_needs_at_least_one_list_item(self):
        document = razor_report(keep=[], delete=[], ask_user=[])
        self.assert_rejected("review-razor.json", document, "三张清单合计至少要有一项")

    def test_razor_keep_may_be_empty_when_delete_has_an_item(self):
        deletion = [{"entity": "unused.py", "loss": "失去过时脚本"}]
        document = razor_report(verdict="改了就过", keep=[], delete=deletion)
        self.assertEqual(self.run_report("review-razor.json", document), (0, []))

    def test_razor_verdict_depends_on_delete_items_only(self):
        ask = [{"entity": "option", "now": "三个分支", "change_to": "删除一个", "saving": "少一个分支"}]
        self.assertEqual(self.run_report("review-razor.json", razor_report(ask_user=ask)), (0, []))
        deletion = [{"entity": "unused.py", "loss": "失去过时脚本"}]
        self.assert_rejected("review-razor.json", razor_report(delete=deletion), "通过」但 delete.items")
        self.assert_rejected("review-razor.json", razor_report(verdict="不通过"), "但 delete.items 为空")

    def test_diagnosis_report_validates_structure_and_actions(self):
        self.create_round1()
        self.assertEqual(self.run_report("diagnosis.json", diagnosis_report(), folders=("group1",)), (0, []))
        self.assert_rejected("diagnosis.json", diagnosis_report(actions=[]), "actions 不能为空", folders=("group1",))
        self.assert_rejected("diagnosis.json", diagnosis_report(actions=["拆分", "拆分"]), "actions 不能重复",
                             folders=("group1",))
        self.assert_rejected("diagnosis.json", diagnosis_report(actions=["继续"]), "actions 只能包含",
                             folders=("group1",))
        self.assert_rejected("diagnosis.json", diagnosis_report(diagnosis=" \t\n"), "diagnosis 不能为空",
                             folders=("group1",))

    def test_reports_reject_template_placeholders_and_placeholder_fragments(self):
        document = main_report()
        document["first_principles"]["essence"] = "<用户真正要的是什么；若是修错，根源在哪>"
        self.assert_rejected("review-main.json", document, "模板占位符")

        document = main_report()
        document["first_principles"]["essence"] = "用户要的是 <用户真正要的是什么>"
        self.assert_rejected("review-main.json", document, "模板占位符")

        self.create_round1()
        self.assert_rejected("diagnosis.json", diagnosis_report(diagnosis="<这组为什么没过，引用各轮报告的阻断项和证据>"),
                             "模板占位符", folders=("group1",))

    def test_duplicate_json_keys_are_rejected(self):
        text = '{"verdict":"通过","verdict":"不通过"}'
        code, problems = self.run_main(("group1", "round1", "review-main.json"), text)
        self.assertEqual(code, 1)
        self.assertTrue(any("重复键" in problem and "verdict" in problem for problem in problems), problems)

    def test_filename_selects_report_kind_and_unknown_names_fail(self):
        code, problems = self.run_report("review.md", main_report())
        self.assertEqual(code, 1)
        self.assertTrue(any("文件名只能是" in problem for problem in problems), problems)

        self.assert_rejected("review-razor.json", main_report(), "occams_razor")

    def test_invalid_json_and_missing_file_are_reported_without_tracebacks(self):
        code, problems = self.run_main(("group1", "round1", "review-main.json"), '{"verdict":')
        self.assertEqual(code, 1)
        self.assertTrue(any("JSON" in problem for problem in problems), problems)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["check.py", os.path.join(self.root, "group1", "round1", "missing.json")])
        self.assertEqual(code, 1)
        self.assertIn("无法读取", output.getvalue())

    def test_formal_report_location_and_round_are_hard_errors(self):
        cases = [
            (("group1", "round1"), "review-main-old.json", main_report(), "文件名只能是"),
            (("round1",), "review-razor.json", razor_report(), "group<G>/round<R>/"),
            (("group1", "round4"), "review-main.json", main_report(), "只能是 round1、round2、round3"),
            (("group01", "round1"), "review-main.json", main_report(), "group<G>"),
            (("group0", "round1"), "review-main.json", main_report(), "group<G>"),
        ]
        for folders, name, document, expected in cases:
            with self.subTest(path="/".join((*folders, name))):
                code, problems = self.run_report(name, document, folders)
                self.assertEqual(code, 1, problems)
                self.assertTrue(any(expected in problem for problem in problems), problems)

    def test_formal_diagnosis_location_and_filename_are_hard_errors(self):
        self.create_round1()
        report = diagnosis_report()
        self.assertEqual(self.run_report("diagnosis.json", report, ("group1",)), (0, []))
        code, problems = self.run_report("cause.json", report, ("group1",))
        self.assertEqual(code, 1)
        self.assertTrue(any("文件名只能是" in problem for problem in problems), problems)

        code, problems = self.run_report("diagnosis.json", report, ("group1", "round1"))
        self.assertEqual(code, 1)
        self.assertTrue(any("group<G>/diagnosis.json" in problem for problem in problems), problems)

    def test_formal_group_must_start_with_round1(self):
        code, problems = self.run_report("diagnosis.json", diagnosis_report(), ("group1",))
        self.assertEqual(code, 1)
        self.assertTrue(any("缺少 round1" in problem for problem in problems), problems)

    def test_formal_round_directories_are_continuous_and_limited_to_three(self):
        self.run_report("review-main.json", main_report(), ("group1", "round1"))
        code, problems = self.run_report("review-main.json", main_report(), ("group1", "round3"))
        self.assertEqual(code, 1)
        self.assertTrue(any("缺少 round2" in problem for problem in problems), problems)

        for invalid_round in ("round0", "round02", "round4"):
            with self.subTest(round=invalid_round):
                os.makedirs(os.path.join(self.root, "group1", invalid_round), exist_ok=True)
                code, problems = self.run_report("review-main.json", main_report(), ("group1", "round1"))
                self.assertEqual(code, 1)
                self.assertTrue(any(invalid_round in problem for problem in problems), problems)

    def test_opening_a_group_requires_previous_diagnosis_file(self):
        folders = ("group2", "round1")
        code, problems = self.run_report("review-main.json", main_report(), folders)
        self.assertEqual(code, 1)
        self.assertTrue(any("一组要复审到第 3 轮" in problem for problem in problems), problems)

        os.makedirs(os.path.join(self.root, "group1", "round1"), exist_ok=True)
        with open(os.path.join(self.root, "group1", "diagnosis.json"), "w", encoding="utf-8") as stream:
            stream.write("此文件存在即可，不读取内容。")
        self.assertEqual(self.run_report("review-main.json", main_report(), folders), (0, []))

    def test_round3_failure_is_only_a_reminder(self):
        self.create_round1()
        os.makedirs(os.path.join(self.root, "group1", "round2"), exist_ok=True)
        document = main_report(verdict="不通过", hard_to_misuse=[{"problem": "缺陷", "evidence": "check.py:5"}])
        code, output = self.run_report("review-main.json", document, ("group1", "round3"))
        self.assertEqual(code, 0, output)
        self.assertTrue(any("先派判因" in line for line in output), output)

    def test_exit_codes(self):
        self.assertEqual(self.run_report("review-main.json", main_report())[0], 0)
        self.assertEqual(self.run_report("review-main.json", main_report(verdict="坏结论"))[0], 1)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["check.py"]), 2)


if __name__ == "__main__":
    unittest.main()
