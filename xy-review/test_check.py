import contextlib
import io
import os
import tempfile
import unittest

from check import check, main


def main_report(**overrides):
    """主审的审查报告。"""
    fields = {
        "本质": "用户要的是 X",
        "偏离": "无，对照了 spec 第 1、2 条",
        "问题": "无，试了两种用错的写法都当场报错",
        "规范": "\n".join(f"{i}. 无，查了 Y" for i in range(1, 8)),
        "结论": "通过",
    }
    fields.update(overrides)
    return (
        "## 铁律 1 第一性原理\n"
        f"本质：{fields['本质']}\n偏离：{fields['偏离']}\n"
        "## 铁律 2 难误用\n"
        f"问题：{fields['问题']}\n"
        "## 仓库规范\n"
        f"{fields['规范']}\n"
        "## 结论\n"
        f"{fields['结论']}\n"
    )


def razor_report(**overrides):
    """剃刀的剃刀报告。"""
    fields = {
        "留下清单": "参数 x：离开它，用户原话 1「参数可配置」",
        "删除清单": "无，逐样问过",
        "要用户定": "无，逐项判断后没有拿不准的项",
        "结论": "通过",
    }
    fields.update(overrides)
    return (
        "## 铁律 3 奥卡姆剃刀\n"
        f"留下清单：{fields['留下清单']}\n删除清单：{fields['删除清单']}\n要用户定：{fields['要用户定']}\n"
        "## 结论\n"
        f"{fields['结论']}\n"
    )


def diagnosis_report(diagnosis, conclusion):
    """判因报告，正文和结论都显式传入。"""
    return f"## 判因\n{diagnosis}\n## 结论\n{conclusion}\n"


def write_report(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class CheckTest(unittest.TestCase):
    def assertProblems(self, text, *needles):
        problems = check(text)
        self.assertTrue(problems, "应该报错却通过了")
        for needle in needles:
            self.assertTrue(any(needle in p for p in problems), f"没报「{needle}」：{problems}")

    def run_main(self, path):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["check.py", path])
        return code, output.getvalue().splitlines()

    def test_clean_reports_pass(self):
        self.assertEqual(check(main_report()), [])
        self.assertEqual(check(razor_report()), [])

    def test_diagnosis_report_passes(self):
        self.assertEqual(check(diagnosis_report("三轮审查仍混合多个目标，见 group1/round3。", "拆分、重想、问用户、顶回")), [])

    def test_diagnosis_extra_section(self):
        text = diagnosis_report("三轮审查仍混合多个目标，见 group1/round3。", "拆分")
        self.assertProblems(text + "## 备注\n补充说明\n", "不认识的节「## 备注」")

    def test_diagnosis_conclusion_single_line(self):
        self.assertProblems(diagnosis_report("三轮审查仍混合多个目标，见 group1/round3。", "拆分\n重想"), "判因结论只能有一行")

    def test_diagnosis_action_must_be_known(self):
        self.assertProblems(diagnosis_report("三轮审查仍混合多个目标，见 group1/round3。", "通过"), "判因结论", "走法")

    def test_diagnosis_actions_must_not_repeat(self):
        self.assertProblems(diagnosis_report("三轮审查仍混合多个目标，见 group1/round3。", "拆分、拆分"), "判因结论", "不重复")

    def test_diagnosis_must_not_be_empty(self):
        self.assertProblems(diagnosis_report(" \n\t", "拆分"), "判因为空")

    def test_diagnosis_must_not_copy_placeholder(self):
        self.assertProblems(diagnosis_report("<用户真正要的是什么；若是修错，根源在哪>", "拆分"), "判因还留着模板占位符")

    def test_fail_with_findings(self):
        self.assertEqual(check(main_report(偏离="a.py:3 没改根源，在 b.py 兜底", 结论="不通过")), [])
        self.assertEqual(check(razor_report(删除清单="- 参数 x；删了丢什么：无\n- 分支 y；删了丢什么：无", 结论="改了就过")), [])

    def test_report_must_match_one_kind(self):
        self.assertProblems("## 结论\n通过\n", "要么是审查报告")
        mixed = main_report().replace("## 结论\n", "## 铁律 3 奥卡姆剃刀\n留下清单：a：原话 1\n删除清单：无，问过\n要用户定：无，没有\n## 结论\n")
        self.assertProblems(mixed, "不认识的节「## 铁律 3 奥卡姆剃刀」")

    def test_missing_section(self):
        self.assertProblems(main_report().replace("## 铁律 2 难误用\n", ""), "缺少「## 铁律 2 难误用」")
        self.assertProblems(razor_report().replace("## 结论\n通过\n", ""), "缺少「## 结论」")

    def test_unknown_section(self):
        self.assertProblems(main_report() + "## 备注\n随便写\n", "不认识的节")
        self.assertProblems(razor_report() + "## 仓库规范\n1. 无，查了\n", "不认识的节「## 仓库规范」")

    def test_duplicate_section(self):
        text = main_report().replace("## 铁律 2 难误用\n", "## 铁律 2 难误用\n问题：check.py:100 漏检\n## 铁律 2 难误用\n")
        self.assertProblems(text, "「## 铁律 2 难误用」出现了两次")

    def test_content_outside_fields_is_rejected(self):
        finding = "- check.py:35 发现被丢弃\n"
        self.assertProblems(finding + main_report(), "第一节之前不能有内容")
        self.assertProblems(main_report().replace("## 铁律 2 难误用\n", "## 铁律 2 难误用\n" + finding), "字段之外有内容")
        self.assertProblems(main_report().replace("## 仓库规范\n", "## 仓库规范\n" + finding), "第 1 条之前有内容")

    def test_empty_field(self):
        self.assertProblems(main_report(偏离=""), "「偏离」为空")

    def test_missing_field(self):
        self.assertProblems(main_report().replace("偏离：无，对照了 spec 第 1、2 条\n", ""), "缺少「偏离」")

    def test_duplicate_field(self):
        self.assertProblems(main_report(问题="check.py:100 漏检\n问题：无，查了"), "「问题」出现了两次")

    def test_nonstandard_field_line(self):
        self.assertProblems(main_report(问题="无，核过\n问题: a.py:10 仍漏检"), "全角冒号")
        self.assertProblems(main_report(问题="无，核过\n**问题**：a.py:10 仍漏检"), "全角冒号")
        self.assertProblems(main_report().replace("本质：用户要的是 X", "本质: 用户要的是 X"), "全角冒号", "缺少「本质」")

    def test_list_item_named_like_a_field_is_content(self):
        self.assertEqual(check(razor_report(留下清单="\n- 删除清单：原话 8 要求说明删了丢什么")), [])
        self.assertEqual(check(razor_report(留下清单="\n  - 删除清单：原话 8 要求说明删了丢什么")), [])

    def test_placeholder_whole_field(self):
        self.assertProblems(main_report(本质="<用户真正要的是什么；若是修错，根源在哪>"), "占位符")

    def test_placeholder_fragment_inside_line(self):
        self.assertProblems(razor_report(留下清单="参数 x：<离开它，用户原话哪条做不到>"), "占位符")

    def test_angle_brackets_outside_template_are_fine(self):
        text = main_report(问题="依据 </home/xy/.claude/xy-workflow/原话.md> 的原话 7，缺少格式校验", 结论="改了就过")
        self.assertEqual(check(text), [])
        text = main_report(偏离="目录 ~/.claude/xy-workflow/<仓库名>-<主题>/ 第 8 步删了，账本还指着它", 结论="改了就过")
        self.assertEqual(check(text), [])

    def test_bare_none_needs_comparison(self):
        self.assertProblems(main_report(偏离="无"), "后面要写对照了什么")
        self.assertProblems(main_report(偏离="无（）"), "后面要写对照了什么")

    def test_none_must_be_single_line(self):
        self.assertProblems(razor_report(删除清单="无，逐样问过\n参数 x；删了丢什么：无"), "只能有这一行")
        self.assertProblems(main_report(偏离="无，对照了 X\ncheck.py:12 没改根源"), "只能有这一行")

    def test_none_with_parentheses_or_bullet_is_none(self):
        self.assertEqual(check(main_report(偏离="无（对照了 spec）")), [])
        self.assertEqual(check(main_report(偏离="\n- 无，对照了 spec")), [])

    def test_essence_and_keep_list_cannot_be_none(self):
        self.assertProblems(main_report(本质="无，看不出"), "「本质」不能写「无」")
        self.assertProblems(razor_report(留下清单="无，没什么要留"), "「留下清单」不能写「无」")

    def test_space_after_none_is_a_finding(self):
        self.assertProblems(main_report(问题="无 docstring 的函数 check_lines（check.py:120）"), "结论写通过，但有的地方不是「无，」开头")

    def test_word_starting_with_none_is_a_finding(self):
        self.assertProblems(main_report(问题="无法拒绝空路径，check.py:138 直接打开"),
                            "结论写通过，但有的地方不是「无，」开头：「问题」（无法拒绝空路径")

    def test_rule_missing(self):
        self.assertProblems(main_report(规范="1. 无，查了\n…\n7. 无，查了"), "第 2 条缺失", "第 6 条缺失")

    def test_rule_duplicate(self):
        text = main_report(规范="1. check.py:100 漏检\n1. 无，查了\n" + "\n".join(f"{i}. 无，查了" for i in range(2, 8)))
        self.assertProblems(text, "第 1 条出现了两次")

    def test_rule_out_of_range(self):
        self.assertProblems(main_report(规范="\n".join(f"{i}. 无，查了" for i in range(1, 9))), "只有 1 到 7 条")

    def test_conclusion_single_line(self):
        self.assertProblems(main_report(结论="通过；删除清单 0 条"), "结论只能有一行")
        self.assertProblems(razor_report(结论="通过\n### Assessment\nReady"), "结论只能有一行")

    def test_pass_with_findings(self):
        self.assertProblems(main_report(问题="缺省值静默生效"), "结论写通过")
        self.assertProblems(razor_report(删除清单="参数 x；删了丢什么：无"), "结论写通过，但有的地方不是「无，」开头：「删除清单」")

    def test_fix_and_pass_needs_findings(self):
        self.assertEqual(check(main_report(问题="缺省值静默生效", 结论="改了就过")), [])
        self.assertProblems(main_report(结论="改了就过"), "结论写改了就过，但各节都是「无」")

    def test_fail_without_findings(self):
        self.assertProblems(main_report(结论="不通过"), "结论写不通过")

    def test_keep_line_without_reason(self):
        self.assertProblems(razor_report(留下清单="参数 x：原话 1 要求可配置\n分支 y"), "留下清单每行")

    def test_delete_line_without_loss(self):
        self.assertProblems(razor_report(删除清单="参数 x", 结论="不通过"), "删除清单每行")

    def test_list_marker_is_not_an_entity(self):
        self.assertProblems(razor_report(留下清单="- ：用户要求必须保留"), "留下清单每行")
        self.assertProblems(razor_report(删除清单="- ；删了不丢要求", 结论="不通过"), "删除清单每行")

    def test_user_decisions_do_not_block(self):
        self.assertEqual(check(razor_report(要用户定="old.py：4 行;保留现状；省 1 行")), [])

    def test_user_decision_requires_entity_and_each_details_segment(self):
        incomplete = [
            "：4 行；保留现状；省 1 行",
            "old.py：；保留现状；省 1 行",
            "old.py：4 行；；省 1 行",
            "old.py：4 行；保留现状；",
        ]
        for value in incomplete:
            with self.subTest(value=value):
                self.assertProblems(razor_report(要用户定=value))

    def test_user_decision_line_requires_entity_and_details(self):
        self.assertProblems(
            razor_report(要用户定="old.py 不确定能否删"),
            "要用户定每行",
            "实体：现在多大；改成什么；能省多少",
        )

    def test_formal_review_location_and_filename_are_hard_errors(self):
        cases = [
            (("group1", "round1"), "review.md", main_report(), "review-main.md"),
            (("group1", "round1"), "review-razor.md", main_report(), "review-main.md"),
            (("group1", "round1"), "review.md", razor_report(), "review-razor.md"),
            (("group1", "round1"), "review-main.md", razor_report(), "review-razor.md"),
            (("round1",), "review-razor.md", razor_report(), "group<G>/round<R>/"),
            (("group1", "round4"), "review-main.md", main_report(), "只能是 round1、round2、round3"),
            (("group01", "round1"), "review-main.md", main_report(), "group<G>"),
            (("group0", "round1"), "review-main.md", main_report(), "group<G>"),
        ]
        for parts, name, report, expected in cases:
            with self.subTest(path="/".join(parts + (name,))), tempfile.TemporaryDirectory() as root:
                path = os.path.join(root, *parts, name)
                write_report(path, report)
                code, problems = self.run_main(path)
                self.assertEqual(code, 1, problems)
                self.assertTrue(any(expected in line for line in problems), problems)

    def test_formal_diagnosis_location_and_filename_are_hard_errors(self):
        report = diagnosis_report("三轮仍未通过，见 group1/round3。", "重想")
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "group1", "round1"))
            correct = os.path.join(root, "group1", "diagnosis.md")
            write_report(correct, report)
            self.assertEqual(self.run_main(correct), (0, []))

            wrong_name = os.path.join(root, "group1", "cause.md")
            write_report(wrong_name, report)
            code, problems = self.run_main(wrong_name)
            self.assertEqual(code, 1)
            self.assertTrue(any("diagnosis.md" in line for line in problems), problems)

            wrong_location = os.path.join(root, "group1", "round1", "diagnosis.md")
            write_report(wrong_location, report)
            code, problems = self.run_main(wrong_location)
            self.assertEqual(code, 1)
            self.assertTrue(any("group<G>/diagnosis.md" in line for line in problems), problems)

    def test_formal_group_must_start_with_round1(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "group1", "diagnosis.md")
            write_report(path, diagnosis_report("缺少 round1。", "重想"))
            code, problems = self.run_main(path)
        self.assertEqual(code, 1)
        self.assertTrue(any("缺少 round1" in line for line in problems), problems)

    def test_formal_round_directories_are_continuous_and_limited_to_three(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "group1", "round3", "review-main.md")
            write_report(path, main_report())
            os.makedirs(os.path.join(root, "group1", "round1"))
            code, problems = self.run_main(path)
            self.assertEqual(code, 1)
            self.assertTrue(any("缺少 round2" in line for line in problems), problems)

        for invalid_round in ("round0", "round02", "round4"):
            with self.subTest(round=invalid_round), tempfile.TemporaryDirectory() as root:
                path = os.path.join(root, "group1", "round1", "review-main.md")
                write_report(path, main_report())
                os.makedirs(os.path.join(root, "group1", invalid_round))
                code, problems = self.run_main(path)
                self.assertEqual(code, 1)
                self.assertTrue(any(invalid_round in line for line in problems), problems)

    def test_opening_a_group_requires_previous_diagnosis_only(self):
        with tempfile.TemporaryDirectory() as root:
            group1 = os.path.join(root, "group1")
            group2_report = os.path.join(root, "group2", "round1", "review-main.md")
            write_report(group2_report, main_report())

            code, problems = self.run_main(group2_report)
            self.assertEqual(code, 1)
            self.assertTrue(any("一组要复审到第 3 轮仍不通过、派判因写下原因后才能开新组" in line
                                for line in problems), problems)

            os.makedirs(group1, exist_ok=True)
            write_report(os.path.join(group1, "diagnosis.md"), "这份文件的内容不读取。\n")
            write_report(os.path.join(group1, "round3", "review-main.md"), "故意写成无效内容\n")
            self.assertEqual(self.run_main(group2_report), (0, []))

    def test_round3_failure_reminder_is_not_a_hard_error(self):
        cases = [("round3", "不通过", True), ("round3", "改了就过", False), ("round2", "不通过", False)]
        for round_name, conclusion, expected_reminder in cases:
            with self.subTest(round=round_name, conclusion=conclusion), tempfile.TemporaryDirectory() as root:
                group = os.path.join(root, "group1")
                for number in range(1, int(round_name[-1]) + 1):
                    os.makedirs(os.path.join(group, f"round{number}"))
                path = os.path.join(group, round_name, "review-main.md")
                write_report(path, main_report(问题="check.py:100 漏检", 结论=conclusion))
                code, output = self.run_main(path)
                self.assertEqual(code, 0, output)
                if expected_reminder:
                    self.assertTrue(any("先派判因" in line for line in output), output)
                else:
                    self.assertFalse(any(line.startswith("提醒：") for line in output), output)

    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as root:
            cases = [
                ("group1/round1/review-main.md", main_report(), 0),
                ("group1/round1/review-razor.md", razor_report(删除清单="参数 x；丢什么：无", 结论="改了就过"), 0),
                ("group1/round1/review-main.md", main_report(偏离=""), 1),
            ]
            for relative_path, text, expected in cases:
                with self.subTest(path=relative_path, expected=expected):
                    path = os.path.join(root, relative_path)
                    write_report(path, text)
                    code, _ = self.run_main(path)
                    self.assertEqual(code, expected)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["check.py"]), 2)


if __name__ == "__main__":
    unittest.main()
