"""xy-review JSON 报告的结构、内容和正式位置测试。"""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import rule_references
import check
from check import REPO_RULE_NUMBERS, main


def section(items=None, checked="核对了实现与 spec"):
    return {"items": [] if items is None else items, "checked": checked}


def main_report(verdict="通过", deviations=None, hard_to_misuse=None, repo_rules=None):
    return {
        "first_principles": {
            "essence": "用户要的是明确且可检查的行为",
            "deviations": section(deviations),
            "quote_problems": section(),
        },
        "hard_to_misuse": section(hard_to_misuse),
        "repo_rules": {str(number): section() for number in REPO_RULE_NUMBERS} if repo_rules is None else repo_rules,
        "verdict": verdict,
    }


def razor_report(verdict="通过", keep=None, keep_old=None, delete=None, ask_user=None):
    return {
        "occams_razor": {
            "keep": [{
                "entity": "check.py",
                "requirement": "用户原话 1「检查改动」",
                "achieved": "检查改动是否符合要求",
                "cost": "实现简单，代价配得上收益",
            }] if keep is None else keep,
            "keep_old": [] if keep_old is None else keep_old,
            "delete": {"items": [] if delete is None else delete, "checked": "核对了依赖与调用方"},
            "ask_user": {"items": [] if ask_user is None else ask_user, "checked": "核对了行为变化"},
            "quote_problems": section(),
        },
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

    def create_formal_round3(self, failed=False, group="group1"):
        group_path = Path(self.root) / group
        for number in (1, 2, 3):
            (group_path / f"round{number}").mkdir(parents=True, exist_ok=True)
        reports = [
            ("review-main.json", main_report()),
            ("review-razor.json", razor_report()),
        ]
        if failed:
            reports[0] = ("review-main.json", main_report(verdict="不通过", hard_to_misuse=[{
                "problem": "缺陷",
                "evidence": "check.py:5",
                "quote": {"source": "xy-repo-rules 第 7 条", "text": "积极删除或合并不必要的代码或文档"},
            }]))
        for name, report in reports:
            code, output = self.run_report(name, report, (group, "round3"))
            self.assertEqual(code, 0, output)

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
        document["first_principles"]["deviations"]["items"] = [{
            "problem": "问题",
            "quote": {"source": "Q94", "text": "review的结果…是否用json更好？"},
        }]
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

    def test_main_report_requires_quote_problems(self):
        document = main_report()
        del document["first_principles"]["quote_problems"]
        self.assert_rejected(
            "review-main.json", document,
            "缺少字段：报告.first_principles.quote_problems",
        )

    def test_quote_problems_require_suggestion(self):
        finding = {
            "problem": "两条原话互相矛盾",
            "evidence": "与原话 1「必须启用」相矛盾",
            "quote": {"source": "Q2", "text": "默认关闭"},
        }
        document = main_report(verdict="改了就过")
        document["first_principles"]["quote_problems"]["items"] = [finding]
        self.assert_rejected(
            "review-main.json", document,
            "缺少字段：报告.first_principles.quote_problems.items[0].suggestion",
        )

    def test_quote_problems_are_main_verdict_findings(self):
        finding = {
            "problem": "两条原话互相矛盾",
            "evidence": "与原话 1「必须启用」相矛盾",
            "quote": {"source": "Q2", "text": "默认关闭"},
            "suggestion": "请用户决定是否启用，并说明理由",
        }
        document = main_report()
        document["first_principles"]["quote_problems"]["items"] = [finding]
        self.assert_rejected(
            "review-main.json", document,
            "通过」但有发现", "first_principles.quote_problems",
        )

    def test_quote_problems_can_be_the_only_findings_for_changed_verdict(self):
        finding = {
            "problem": "两条原话互相矛盾",
            "evidence": "与原话 1「必须启用」相矛盾",
            "quote": {"source": "Q2", "text": "默认关闭"},
            "suggestion": "请用户决定是否启用，并说明理由",
        }
        document = main_report(verdict="改了就过")
        document["first_principles"]["quote_problems"]["items"] = [finding]
        self.assertEqual(self.run_report("review-main.json", document), (0, []))

    def test_main_report_items_require_nonblank_quotes(self):
        def finding():
            return {
                "problem": "发现一项阻断",
                "evidence": "check.py:25",
                "quote": {"source": "Q94", "text": "review的结果…是否用json更好？"},
            }

        cases = []
        item = finding()
        del item["quote"]
        cases.append((item, "缺少字段：报告.first_principles.deviations.items[0].quote"))

        item = finding()
        del item["quote"]["source"]
        cases.append((item, "缺少字段：报告.first_principles.deviations.items[0].quote.source"))

        item = finding()
        del item["quote"]["text"]
        cases.append((item, "缺少字段：报告.first_principles.deviations.items[0].quote.text"))

        item = finding()
        item["quote"]["source"] = " \n\t"
        cases.append((item, "报告.first_principles.deviations.items[0].quote.source 不能为空"))

        item = finding()
        item["quote"]["text"] = " \n\t"
        cases.append((item, "报告.first_principles.deviations.items[0].quote.text 不能为空"))

        for item, message in cases:
            with self.subTest(message=message):
                document = main_report(verdict="改了就过", deviations=[item])
                self.assert_rejected("review-main.json", document, message)

        document = main_report(verdict="改了就过", deviations=[finding()])
        self.assertEqual(self.run_report("review-main.json", document), (0, []))

    def test_main_report_rejects_rule_quote_not_found_in_the_named_rule(self):
        finding = {
            "problem": "发现一项阻断",
            "evidence": "check.py:25",
            "quote": {"source": "xy-iron-law 第 1 条", "text": "不是该条原文"},
        }
        document = main_report(verdict="改了就过", deviations=[finding])
        self.assert_rejected("review-main.json", document, "quote.text", "xy-iron-law 第 1 条")

    def test_main_report_rejects_malformed_rule_source_with_wrong_quote(self):
        finding = {
            "problem": "发现一项阻断",
            "evidence": "check.py:25",
            "quote": {"source": "xy-iron-law第 1 条", "text": "错误引文"},
        }
        document = main_report(verdict="改了就过", deviations=[finding])
        self.assert_rejected("review-main.json", document, "格式错误或条号不存在")

    def test_main_report_rejects_rule_name_anywhere_in_source(self):
        sources = (
            " xy-iron-law 第 1 条",
            "见 xy-iron-law 第 1 条",
            str(rule_references.RULE_FILES["xy-iron-law"]),
        )
        for source in sources:
            with self.subTest(source=source):
                finding = {
                    "problem": "发现一项阻断",
                    "evidence": "check.py:25",
                    "quote": {"source": source, "text": "错误引文"},
                }
                document = main_report(verdict="改了就过", deviations=[finding])
                self.assert_rejected("review-main.json", document,
                                     "格式错误或条号不存在")

    def test_main_report_accepts_contract_number_and_quote_file_path_sources(self):
        sources = ("Q94", "xy-goal/quotes/50.md")
        findings = [
            {
                "problem": "发现一项阻断",
                "evidence": "check.py:25",
                "quote": {"source": source, "text": "原话原文"},
            }
            for source in sources
        ]
        document = main_report(verdict="改了就过", deviations=findings)
        self.assertEqual(self.run_report("review-main.json", document), (0, []))

    def test_check_cannot_build_zero_repository_rule_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing_path = Path(temporary) / "missing-rules.md"
            try:
                with patch.dict(rule_references.RULE_FILES,
                                {"xy-repo-rules": missing_path}):
                    rule_references._numbered_rules.cache_clear()
                    with self.assertRaises(ValueError):
                        importlib.reload(check)
            finally:
                rule_references._numbered_rules.cache_clear()
                importlib.reload(check)

    def test_main_report_accepts_a_continuous_quote_from_the_named_rule(self):
        finding = {
            "problem": "发现一项阻断",
            "evidence": "check.py:25",
            "quote": {"source": "xy-iron-law 第 1 条", "text": "第一性原理：思考"},
        }
        document = main_report(verdict="改了就过", deviations=[finding])
        self.assertEqual(self.run_report("review-main.json", document), (0, []))

    def test_main_report_accepts_new_iron_law_rule_three_quote(self):
        finding = {
            "problem": "新增要求没有证据",
            "evidence": "spec.md:12",
            "quote": {
                "source": "xy-iron-law 第 3 条",
                "text": "用户原话、功能清单，或写了证据的派方需求",
            },
        }
        document = main_report(verdict="改了就过", deviations=[finding])
        self.assertEqual(self.run_report("review-main.json", document), (0, []))

    def test_main_report_requires_each_numbered_repository_rule(self):
        rules = {str(number): section() for number in REPO_RULE_NUMBERS}
        missing = str(REPO_RULE_NUMBERS[-1])
        del rules[missing]
        rules[str(REPO_RULE_NUMBERS[-1] + 1)] = section()
        document = main_report(repo_rules=rules)
        self.assert_rejected("review-main.json", document,
                             f"缺少字段：报告.repo_rules.{missing}",
                             f"repo_rules 多余字段：{REPO_RULE_NUMBERS[-1] + 1}")

    def test_main_verdict_matches_findings(self):
        findings = [{
            "problem": "增量：漏掉新约束；用户原话 4「格式规范」",
            "evidence": "check.py:25",
            "quote": {"source": "Q94", "text": "review的结果…是否用json更好？"},
        }]
        self.assertEqual(self.run_report("review-main.json", main_report(verdict="改了就过", deviations=findings)), (0, []))
        self.assert_rejected("review-main.json", main_report(verdict="通过", deviations=findings), "通过」但有发现")
        self.assert_rejected("review-main.json", main_report(verdict="不通过"), "但没有发现")
        self.assert_rejected("review-main.json", main_report(verdict="未知"), "verdict 只能是")

    def test_razor_report_requires_exact_keys_types_and_nonblank_strings(self):
        cases = []
        document = razor_report()
        document["occams_razor"]["delete"]["extra"] = "多余"
        cases.append((document, "occams_razor.delete 多余字段：extra"))

        document = razor_report()
        document["occams_razor"]["keep"] = [{
            "entity": "check.py",
            "requirement": " ",
            "achieved": "检查改动",
            "cost": "代价合适",
        }]
        cases.append((document, "报告.occams_razor.keep[0].requirement 不能为空"))

        document = razor_report()
        document["occams_razor"]["ask_user"]["items"] = [{"entity": "文件"}]
        cases.append((document, "缺少字段：报告.occams_razor.ask_user.items[0].now"))

        document = razor_report()
        document["occams_razor"]["delete"]["checked"] = None
        cases.append((document, "报告.occams_razor.delete.checked 必须是字符串"))

        for document, message in cases:
            with self.subTest(message=message):
                self.assert_rejected("review-razor.json", document, message)

    def test_razor_report_requires_quote_problems(self):
        document = razor_report()
        del document["occams_razor"]["quote_problems"]
        self.assert_rejected(
            "review-razor.json", document,
            "缺少字段：报告.occams_razor.quote_problems",
        )

    def test_razor_quote_problems_reject_wrong_item_keys(self):
        document = razor_report()
        document["occams_razor"]["quote_problems"]["items"] = [{
            "problem": "原话把实现细节写成了必须项",
            "evidence": "spec.md:12",
            "quote": {"source": "Q1", "text": "用户原话"},
            "suggested": "建议用户决定",
        }]
        self.assert_rejected(
            "review-razor.json", document,
            "缺少字段：报告.occams_razor.quote_problems.items[0].suggestion",
            "多余字段：suggested",
        )

    def test_razor_quote_problems_check_rule_quotes_verbatim(self):
        document = razor_report()
        document["occams_razor"]["quote_problems"]["items"] = [{
            "problem": "原话要求的东西没有必要",
            "evidence": "spec.md:12",
            "quote": {
                "source": "xy-iron-law 第 3 条",
                "text": "删了它，哪个要求做不到",
            },
            "suggestion": "请用户决定是否保留这项要求",
        }]
        self.assert_rejected(
            "review-razor.json", document,
            "报告.occams_razor.quote_problems.items[0].quote.text",
            "必须逐字引用「xy-iron-law 第 3 条」",
        )

    def test_razor_report_needs_at_least_one_list_item(self):
        document = razor_report(keep=[], keep_old=[], delete=[], ask_user=[])
        self.assert_rejected("review-razor.json", document, "清单合计至少要有一项")

    def test_razor_quote_problems_alone_satisfy_nonempty_list_rule(self):
        document = razor_report(keep=[], keep_old=[], delete=[], ask_user=[])
        document["occams_razor"]["quote_problems"]["items"] = [{
            "problem": "原话要求的东西没有必要",
            "evidence": "spec.md:12",
            "quote": {"source": "Q1", "text": "用户原话"},
            "suggestion": "请用户决定是否保留",
        }]
        self.assertEqual(self.run_report("review-razor.json", document), (0, []))

    def test_razor_rejects_old_keep_shape(self):
        document = razor_report(keep=[{"entity": "check.py", "reason": "仍在使用"}])
        self.assert_rejected(
            "review-razor.json", document,
            "缺少字段：报告.occams_razor.keep[0].requirement",
            "多余字段：reason",
        )

    def test_razor_rejects_keep_old_with_wrong_keys(self):
        document = razor_report(keep=[], keep_old=[{"entity": "check.py", "why": "入口"}])
        self.assert_rejected(
            "review-razor.json", document,
            "缺少字段：报告.occams_razor.keep_old[0].reason",
            "多余字段：why",
        )

    def test_razor_keep_old_alone_satisfies_the_nonempty_list_rule(self):
        document = razor_report(
            keep=[], keep_old=[{"entity": "check.py", "reason": "从当前入口可达"}],
        )
        self.assertEqual(self.run_report("review-razor.json", document), (0, []))

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
        self.create_formal_round3(failed=True)
        self.assertEqual(self.run_report("diagnosis.json", diagnosis_report(), folders=("group1",)), (0, []))
        self.assert_rejected("diagnosis.json", diagnosis_report(actions=[]), "actions 不能为空", folders=("group1",))
        self.assert_rejected("diagnosis.json", diagnosis_report(actions=["拆分", "拆分"]), "actions 不能重复",
                             folders=("group1",))
        self.assert_rejected("diagnosis.json", diagnosis_report(actions=["继续"]), "actions 只能包含",
                             folders=("group1",))
        self.assert_rejected("diagnosis.json", diagnosis_report(diagnosis=" \t\n"), "diagnosis 不能为空",
                             folders=("group1",))

    def test_informal_diagnosis_uses_the_shared_report_validation(self):
        directory = Path(self.root) / "informal-loop"
        directory.mkdir()
        (directory / "report-round4.md").write_text("改测记录", encoding="utf-8")
        self.assertEqual(self.run_report("diagnosis.json", diagnosis_report(),
                                         folders=("informal-loop",)), (0, []))
        self.assert_rejected("diagnosis.json", diagnosis_report(actions=[]), "actions 不能为空",
                             folders=("informal-loop",))

    def test_informal_diagnosis_requires_a_valid_report_round_in_its_directory(self):
        directory = Path(self.root) / "informal-loop"
        directory.mkdir()
        (directory / "report-round04.md").write_text("不是合法轮次", encoding="utf-8")
        code, problems = self.run_report("diagnosis.json", diagnosis_report(),
                                         folders=("informal-loop",))
        self.assertEqual(code, 1, problems)
        self.assertTrue(any("判因报告必须写在" in problem for problem in problems), problems)

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

    def test_diagnosis_template_actions_are_rejected_when_copied(self):
        skill = Path(__file__).with_name("SKILL.md").read_text(encoding="utf-8")
        templates = [json.loads(block) for block in re.findall(r"```json\s*\n(.*?)```", skill, re.S)]
        diagnosis_template = next(template for template in templates if "diagnosis" in template)
        self.create_round1()
        copied_actions = {"diagnosis": "引用阻断项和证据说明根因。", "actions": diagnosis_template["actions"]}
        self.assert_rejected("diagnosis.json", copied_actions, "模板占位符", folders=("group1",))

    def test_review_report_guidance_is_not_repeated(self):
        skill = Path(__file__).with_name("SKILL.md").read_text(encoding="utf-8")
        report_section = skill.split("## 报告", 1)[1].split("## 收到报告后", 1)[0]
        self.assertEqual(report_section.count("剃刀结论只看 `delete.items` 是否为空"), 1)
        self.assertEqual(report_section.count("`keep`、`keep_old`、`delete.items`、`ask_user.items`、`quote_problems.items` 合计至少一项"), 1)
        self.assertNotIn("`keep`、`keep_old`、`delete.items`、`ask_user.items` 合计至少一项", report_section)
        self.assertEqual(report_section.count("依据写入 `quote`"), 1)
        self.assertNotIn("在 `problem` 里贴用户原话编号与原文", report_section)

        templates = [json.loads(block) for block in re.findall(r"```json\s*\n(.*?)```", report_section, re.S)]
        razor_template = next(template for template in templates if "occams_razor" in template)
        keep = razor_template["occams_razor"]["keep"][0]
        keep_old = razor_template["occams_razor"]["keep_old"][0]
        self.assertEqual(set(keep), {"entity", "requirement", "achieved", "cost"})
        self.assertIn("定了的要求：用户原话编号加原文、功能清单的条目和原文", keep["requirement"])
        self.assertIn("派方、作者发起时写的证据", keep["requirement"])
        self.assertEqual(set(keep_old), {"entity", "reason"})
        self.assertIn("同一文件里和这次改动不相干的旧东西可以合成一项", keep_old["reason"])
        self.assertIn("entity 写各样的名字", keep_old["reason"])

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

    def test_formal_diagnosis_location_is_a_hard_error(self):
        self.create_formal_round3(failed=True)
        report = diagnosis_report()
        self.assertEqual(self.run_report("diagnosis.json", report, ("group1",)), (0, []))

        code, problems = self.run_report("diagnosis.json", report, ("group1", "round1"))
        self.assertEqual(code, 1)
        self.assertTrue(any("group<G>/diagnosis.json" in problem for problem in problems), problems)

    def test_formal_diagnosis_requires_round3(self):
        self.create_round1()
        os.makedirs(os.path.join(self.root, "group1", "round2"), exist_ok=True)
        code, problems = self.run_report("diagnosis.json", diagnosis_report(), folders=("group1",))
        self.assertEqual(code, 1, problems)
        self.assertTrue(any("必须已有 round3" in problem for problem in problems), problems)

    def test_formal_diagnosis_rejects_third_round_when_all_reports_pass(self):
        self.create_formal_round3()
        code, problems = self.run_report("diagnosis.json", diagnosis_report(), folders=("group1",))
        self.assertEqual(code, 1, problems)
        self.assertTrue(any("至少要有一份结论为「不通过」" in problem for problem in problems), problems)

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

    def test_opening_a_group_requires_a_valid_previous_diagnosis(self):
        folders = ("group2", "round1")
        code, problems = self.run_report("review-main.json", main_report(), folders)
        self.assertEqual(code, 1)
        self.assertTrue(any("一组要复审到第 3 轮" in problem for problem in problems), problems)

        self.create_formal_round3(failed=True)
        with open(os.path.join(self.root, "group1", "diagnosis.json"), "w", encoding="utf-8") as stream:
            stream.write(json_text(diagnosis_report(actions=["无效动作"])))
        code, problems = self.run_report("review-main.json", main_report(), folders)
        self.assertEqual(code, 1, problems)
        self.assertTrue(any("actions 只能包含" in problem for problem in problems), problems)

        with open(os.path.join(self.root, "group1", "diagnosis.json"), "w", encoding="utf-8") as stream:
            stream.write(json_text(diagnosis_report()))
        self.assertEqual(self.run_report("review-main.json", main_report(), folders), (0, []))

    def test_round3_failure_is_only_a_reminder(self):
        self.create_round1()
        os.makedirs(os.path.join(self.root, "group1", "round2"), exist_ok=True)
        document = main_report(verdict="不通过", hard_to_misuse=[{
            "problem": "缺陷",
            "evidence": "check.py:5",
            "quote": {"source": "xy-repo-rules 第 7 条", "text": "积极删除或合并不必要的代码或文档"},
        }])
        code, output = self.run_report("review-main.json", document, ("group1", "round3"))
        self.assertEqual(code, 0, output)
        self.assertTrue(any("先派判因" in line for line in output), output)

    def test_exit_codes(self):
        self.assertEqual(self.run_report("review-main.json", main_report())[0], 0)
        self.assertEqual(self.run_report("review-main.json", main_report(verdict="坏结论"))[0], 1)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["check.py"]), 2)

