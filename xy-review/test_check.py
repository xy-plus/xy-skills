"""check.py 的测试，一个用例守一条规则。运行：cd xy-review && python3 -B -m unittest test_check"""
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
        "留下清单": "参数 x：原话 1 要求可配置",
        "删除清单": "无，逐样问过",
        "旧代码删减": "无，没碰到旧代码",
        "结论": "通过",
    }
    fields.update(overrides)
    return (
        "## 铁律 3 奥卡姆剃刀\n"
        f"留下清单：{fields['留下清单']}\n删除清单：{fields['删除清单']}\n旧代码删减：{fields['旧代码删减']}\n"
        "## 结论\n"
        f"{fields['结论']}\n"
    )


def diagnosis_report(diagnosis, conclusion):
    """判因报告，正文和结论都显式传入。"""
    return f"## 判因\n{diagnosis}\n## 结论\n{conclusion}\n"


class CheckTest(unittest.TestCase):
    def assertProblems(self, text, *needles):
        problems = check(text)
        self.assertTrue(problems, "应该报错却通过了")
        for needle in needles:
            self.assertTrue(any(needle in p for p in problems), f"没报「{needle}」：{problems}")

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
        mixed = main_report().replace("## 结论\n", "## 铁律 3 奥卡姆剃刀\n留下清单：a：原话 1\n删除清单：无，问过\n旧代码删减：无，没有\n## 结论\n")
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

    def test_old_code_does_not_block(self):
        self.assertEqual(check(razor_report(旧代码删减="old.py:10 死分支")), [])

    def test_round3_failure_reminder(self):
        cases = [("round3", "不通过", True), ("round3", "改了就过", False), ("round2", "不通过", False)]
        for round_name, conclusion, expected_reminder in cases:
            with self.subTest(round=round_name, conclusion=conclusion), tempfile.TemporaryDirectory() as root:
                directory = os.path.join(root, "group7", round_name)
                os.makedirs(directory)
                path = os.path.join(directory, "review.md")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(main_report(问题="check.py:100 漏检", 结论=conclusion))
                paths = [(directory, path)]
                if expected_reminder:
                    previous_directory = os.path.join(root, "group7", "round2")
                    os.makedirs(previous_directory)
                    paths += [(directory, "review.md"), (previous_directory, "../round3/review.md")]
                for working_directory, report_path in paths:
                    with self.subTest(path=report_path):
                        output = io.StringIO()
                        with contextlib.chdir(working_directory), contextlib.redirect_stdout(output):
                            code = main(["check.py", report_path])
                        self.assertEqual(code, 0, output.getvalue())
                        if expected_reminder:
                            self.assertIn(
                                "提醒：group7 第 3 轮仍不通过，按 xy-review 先派判因子代理写 group7/diagnosis.md，按结论处理后再开下一组",
                                output.getvalue().splitlines(),
                            )
                        else:
                            self.assertNotIn("提醒：", output.getvalue())

    def test_exit_codes(self):
        cases = [(main_report(), 0), (razor_report(删除清单="参数 x；丢什么：无", 结论="改了就过"), 0), (main_report(偏离=""), 1)]
        for text, expected in cases:
            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
                f.write(text)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["check.py", f.name]), expected)
            finally:
                os.unlink(f.name)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["check.py"]), 2)


if __name__ == "__main__":
    unittest.main()
