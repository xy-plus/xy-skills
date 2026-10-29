"""xy-review JSON 报告的结构、内容和正式位置测试。"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
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

    def write_cleanup_handoff(self, changed):
        contract = Path(self.root) / "contract"
        (contract / "quotes").mkdir(parents=True, exist_ok=True)
        (contract / "quotes" / "1.md").write_text("用户原话\n", encoding="utf-8")
        document = {
            "kind": "清扫",
            "from": {"name": "派方", "short_id": "a1b2c3d4"},
            "reply": "要",
            "contract": str(contract),
            "changed": [{"path": path, "in_use_by": "无"} for path in changed],
        }
        path = Path(self.root) / "handoff.json"
        path.write_text(json_text(document), encoding="utf-8")
        return path

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

    def test_main_report_requires_repository_rules_one_through_seven(self):
        rules = {str(number): section() for number in range(1, 8)}
        del rules["5"]
        rules["8"] = section()
        document = main_report(repo_rules=rules)
        self.assert_rejected("review-main.json", document,
                             "缺少字段：报告.repo_rules.5", "repo_rules 多余字段：8")

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

    def test_cleanup_handoff_requires_absolute_path_or_none(self):
        self.assert_rejected("review-razor.json", razor_report(cleanup_handoff="相对路径/handoff.json"), "绝对路径")

    def test_cleanup_report_covers_every_changed_path(self):
        changed = ["/repo/deleted.py", "用户@主机:/项目/scripts/active.py"]
        handoff = self.write_cleanup_handoff(changed)
        document = razor_report(
            keep=[
                {"entity": "/repo/deleted.py", "reason": "保留处理记录"},
                {"entity": "用户@主机:/项目/scripts/active.py", "reason": "仍在运行"},
            ],
            cleanup_handoff=str(handoff),
        )
        self.assertEqual(self.run_report("review-razor.json", document), (0, []))

    def test_cleanup_report_reports_each_uncovered_path(self):
        changed = ["/repo/first.py", "/repo/second.py"]
        handoff = self.write_cleanup_handoff(changed)
        document = razor_report(
            keep=[{"entity": "/repo/first.py", "reason": "仍有用"}],
            cleanup_handoff=str(handoff),
        )
        self.assert_rejected("review-razor.json", document, "/repo/second.py")

    def test_cleanup_path_coverage_requires_exact_entity_match(self):
        changed = "/a/b"
        handoff = self.write_cleanup_handoff([changed])
        invalid_entities = ("/a/b 的 f 函数", "/a/b文件")
        for entity in invalid_entities:
            with self.subTest(entity=entity):
                document = razor_report(
                    keep=[{"entity": entity, "reason": "应判为未覆盖"}],
                    cleanup_handoff=str(handoff),
                )
                self.assert_rejected("review-razor.json", document, changed)

        document = razor_report(
            keep=[{"entity": "/a/b", "reason": "函数 f 的实现路径"}],
            cleanup_handoff=str(handoff),
        )
        self.assertEqual(self.run_report("review-razor.json", document), (0, []))

    def test_parent_directory_does_not_cover_changed_file(self):
        changed = "/repo/dir/file.py"
        handoff = self.write_cleanup_handoff([changed])
        document = razor_report(
            keep=[{"entity": "/repo/dir", "reason": "目录仍保留"}],
            cleanup_handoff=str(handoff),
        )
        self.assert_rejected("review-razor.json", document, changed)

    def test_none_cleanup_handoff_skips_coverage(self):
        document = razor_report(keep=[{"entity": "没有改动路径", "reason": "一般任务"}], cleanup_handoff="无")
        self.assertEqual(self.run_report("review-razor.json", document), (0, []))

    def test_cleanup_question_recipient_is_decided_by_cleanup_author(self):
        review_skill = Path(__file__).with_name("SKILL.md").read_text(encoding="utf-8")
        handoff_skill = Path(__file__).parents[1].joinpath("xy-handoff", "SKILL.md").read_text(encoding="utf-8")
        checker = Path(__file__).with_name("check.py").read_text(encoding="utf-8")
        self.assertIn("自己决定问用户还是问派方", review_skill)
        self.assertIn("答复前不删", review_skill)
        self.assertIn("不必先告诉派方", review_skill)
        self.assertIn("接手方问的是用户还是本会话，由它定", handoff_skill)
        self.assertIn("没问或没答复就不验收、不允许自删", handoff_skill)
        self.assertIn("发回去要求先询问，也不代用户作答", handoff_skill)
        self.assertIn("ask_user.items 不计入剃刀报告结论", checker)

    def test_xy_goal_cleanup_runs_in_parallel_with_closing_review(self):
        goal_skill = Path(__file__).parents[1].joinpath("xy-goal", "SKILL.md").read_text(encoding="utf-8")
        handoff_skill = Path(__file__).parents[1].joinpath("xy-handoff", "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("除 T1 外没有开放事项", goal_skill)
        self.assertIn("T1 清扫交出去后不等它", goal_skill)
        self.assertIn("两者并行", goal_skill)
        self.assertIn("复核不判 T1", goal_skill)
        self.assertIn("T1 按「清扫」一节收到验收通过再 accept", goal_skill)
        self.assertIn("T1 已验收后", goal_skill)
        self.assertIn("除 xy-goal 清扫外的所有「要」交接任务", handoff_skill)
        self.assertIn("xy-goal 清扫与关闭前复核并行，不阻塞这段集成和整体验收", handoff_skill)
        self.assertIn("仍按 xy-goal 等 T1 清扫验收", handoff_skill)

    def test_cleanup_handoff_must_be_a_valid_cleanup_handoff(self):
        contract = Path(self.root) / "ordinary-contract"
        (contract / "quotes").mkdir(parents=True)
        ordinary = {
            "kind": "普通",
            "from": {"name": "派方", "short_id": "a1b2c3d4"},
            "task": {"what": "任务", "done_when": "完成", "others": "无"},
            "reply": "要",
            "quotes": [{"text": "原话", "source": "/quotes/1.md"}],
            "rules": "无",
            "facts": [],
            "decide_yourself": [],
            "boundaries": [],
        }
        ordinary_path = Path(self.root) / "ordinary.json"
        ordinary_path.write_text(json_text(ordinary), encoding="utf-8")
        document = razor_report(keep=[{"entity": "/repo/file.py", "reason": "理由"}],
                               cleanup_handoff=str(ordinary_path))
        self.assert_rejected("review-razor.json", document, "必须是 kind 为「清扫」的交接")

        cleanup_path = self.write_cleanup_handoff(["/repo/file.py"])
        malformed = json.loads(cleanup_path.read_text(encoding="utf-8"))
        del malformed["changed"]
        cleanup_path.write_text(json_text(malformed), encoding="utf-8")
        document["cleanup_handoff"] = str(cleanup_path)
        self.assert_rejected("review-razor.json", document, "缺少字段：交接说明.changed")

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
        self.assertEqual(report_section.count("`keep`、`delete.items`、`ask_user.items` 至少一项"), 1)
        self.assertNotIn("报告三张清单合起来至少有一项", report_section)
        self.assertEqual(report_section.count("依据写入 `quote`"), 1)
        self.assertNotIn("在 `problem` 里贴用户原话编号与原文", report_section)

        templates = [json.loads(block) for block in re.findall(r"```json\s*\n(.*?)```", report_section, re.S)]
        razor_template = next(template for template in templates if "occams_razor" in template)
        reason = razor_template["occams_razor"]["keep"][0]["reason"]
        self.assertNotIn("实体：", reason)
        self.assertIn("同一文件里和这次改动不相干的旧东西可以合成一项", reason)
        self.assertIn("entity 写各样的名字", reason)

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
        document = main_report(verdict="不通过", hard_to_misuse=[{
            "problem": "缺陷",
            "evidence": "check.py:5",
            "quote": {"source": "xy-repo-rules 第 7 条", "text": "单个文件到 500 行就报警"},
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


if __name__ == "__main__":
    unittest.main()
