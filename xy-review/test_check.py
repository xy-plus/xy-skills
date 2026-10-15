"""check.py 的测试，一个用例守一条规则。运行：cd xy-review && python3 -B -m unittest test_check"""
import contextlib
import io
from pathlib import Path
import shutil
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


class CheckTest(unittest.TestCase):
    def assertProblems(self, text, *needles):
        problems = check(text)
        self.assertTrue(problems, "应该报错却通过了")
        for needle in needles:
            self.assertTrue(any(needle in p for p in problems), f"没报「{needle}」：{problems}")

    def test_clean_reports_pass(self):
        self.assertEqual(check(main_report()), [])
        self.assertEqual(check(razor_report()), [])

    def test_fail_with_findings(self):
        self.assertEqual(check(main_report(偏离="a.py:3 没改根源，在 b.py 兜底", 结论="不通过")), [])
        self.assertEqual(check(razor_report(删除清单="- 参数 x；删了丢什么：无\n- 分支 y；删了丢什么：无", 结论="改了就过")), [])

    def test_report_must_be_one_of_the_two_kinds(self):
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

    def test_exit_codes(self):
        cases = [(main_report(), 0), (razor_report(删除清单="参数 x；丢什么：无", 结论="改了就过"), 0), (main_report(偏离=""), 1)]
        for text, expected in cases:
            with tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / "group1/round1/report.md"
                report.parent.mkdir(parents=True)
                report.write_text(text, encoding="utf-8")
                (report.parent / "submitted-code.txt").write_text("d0348a4\n", encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["check.py", str(report)]), expected)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["check.py"]), 2)


def diagnosis_report(actions):
    return (
        "## 判因\n"
        "round1/main.txt、round2/main.txt、round3/main.txt 都指出参数缺省，"
        "handling.md 只改了调用处；当前 check.py 仍静默接受空参数，同类问题反复。\n"
        "## 结论\n"
        f"{actions}\n"
    )


class FileCheckTest(unittest.TestCase):
    """走真实文件入口；每项拒绝规则撤掉时，对应的 rc 或补齐提示就会失败。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_report(self, relative, text):
        path = self.write(relative, text)
        version = path.parent.relative_to(self.root) / "submitted-version.md"
        self.write(version, "这一轮送审的原文。\n")
        return path

    def write_round(self, group, round_number, failed_role):
        main_text = main_report()
        razor_text = razor_report()
        if failed_role == "主审":
            main_text = main_report(问题="check.py:10 空参数静默生效", 结论="不通过")
        elif failed_role == "剃刀":
            razor_text = razor_report(删除清单="多余分支；删了不丢原话要求", 结论="不通过")
        prefix = f"group{group}/round{round_number}"
        path = self.write_report(f"{prefix}/main.txt", main_text)
        self.write(f"{prefix}/razor.txt", razor_text)
        if failed_role is not None:
            self.write(f"{prefix}/handling.md", "意见：空参数静默生效；处理：修改参数检查，拒绝空值。\n")
        return path

    def write_failed_group(self, group):
        for round_number in (1, 2, 3):
            self.write_round(group, round_number, "主审")
        return self.write(f"group{group}/diagnosis.md", diagnosis_report("重想"))

    def run_check(self, path):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(["check.py", str(path)])
        return rc, output.getvalue()

    def assert_rejected(self, path, *needles):
        rc, output = self.run_check(path)
        self.assertEqual(rc, 1, output)
        for needle in needles:
            self.assertIn(needle, output)

    def test_review_path_requires_exact_group_and_round_directories(self):
        for relative in ("report.md", "group0/round1/report.md", "group01/round1/report.md",
                         "group1/round0/report.md", "group1/round01/report.md",
                         "group1/round1/nested/report.md"):
            with self.subTest(path=relative):
                path = self.write_report(relative, main_report())
                self.assert_rejected(path, "group<G>/round<R>/", "作者")

    def test_diagnosis_path_requires_group_directory(self):
        for relative in ("diagnosis.md", "group0/diagnosis.md", "group1/round1/diagnosis.md",
                         "group1/nested/diagnosis.md"):
            with self.subTest(path=relative):
                path = self.write(relative, diagnosis_report("重想"))
                self.assert_rejected(path, "group<G>/diagnosis.md", "判因子代理")

    def test_fourth_round_requires_diagnosis_then_next_group(self):
        path = self.write_report("group1/round4/main.txt", main_report())
        self.assert_rejected(path, "group1/diagnosis.md", "group2", "作者", "判因子代理")

    def test_fourth_round_in_current_group_blocks_other_reports(self):
        path = self.write_report("group1/round1/main.txt", main_report())
        (self.root / "group1/round4").mkdir()
        self.assert_rejected(path, "round4", "group1/diagnosis.md", "group2", "判因子代理")

    def test_current_round_may_have_only_one_role(self):
        for text in (main_report(), razor_report()):
            with self.subTest(text=text):
                path = self.write_report("group1/round1/report", text)
                self.assertEqual(self.run_check(path), (0, ""))

    def test_roles_follow_content_instead_of_filename_or_extension(self):
        path = self.write_report("group1/round1/razor", main_report())
        self.write("group1/round1/main.txt", razor_report())
        self.write("group1/round1/brief.md", "请主审检查 spec，报告写到本目录。\n")
        self.assertEqual(self.run_check(path), (0, ""))

    def test_duplicate_main_reports_are_rejected(self):
        path = self.write_report("group1/round1/main-a.txt", main_report())
        self.write("group1/round1/main-b.txt", main_report())
        self.assert_rejected(path, "主审", "main-a.txt", "main-b.txt", "作者")

    def test_duplicate_razor_reports_are_rejected(self):
        path = self.write_report("group1/round1/razor-a.txt", razor_report())
        self.write("group1/round1/razor-b.txt", razor_report())
        self.assert_rejected(path, "剃刀", "razor-a.txt", "razor-b.txt", "作者")

    def test_current_report_still_runs_content_checks(self):
        path = self.write_report("group1/round1/main.txt", main_report(本质=""))
        self.assert_rejected(path, "本质", "为空", "主审")

    def test_current_content_checks_cannot_be_skipped_by_artifact_filename(self):
        for filename in ("handling.md", "submitted-report.md"):
            with self.subTest(filename=filename):
                path = self.write_report(f"group1/round1/{filename}", main_report(本质=""))
                self.assert_rejected(path, "本质", "为空", "主审")

    def test_second_round_requires_first_round(self):
        path = self.write_report("group1/round2/main.txt", main_report())
        self.assert_rejected(path, "round1", "主审", "剃刀")

    def test_previous_round_requires_main_report(self):
        self.write_round(1, 1, "剃刀")
        (self.root / "group1/round1/main.txt").unlink()
        path = self.write_report("group1/round2/main.txt", main_report())
        self.assert_rejected(path, "round1", "主审")

    def test_previous_round_requires_razor_report(self):
        self.write_round(1, 1, "主审")
        (self.root / "group1/round1/razor.txt").unlink()
        path = self.write_report("group1/round2/main.txt", main_report())
        self.assert_rejected(path, "round1", "剃刀")

    def test_previous_reports_must_pass_content_checks(self):
        for filename, text, field, role in (
                ("main.txt", main_report(本质=""), "本质", "主审"),
                ("razor.txt", razor_report(留下清单=""), "留下清单", "剃刀")):
            with self.subTest(role=role):
                self.write_round(1, 1, "主审")
                self.write(f"group1/round1/{filename}", text)
                path = self.write_report("group1/round2/main.txt", main_report())
                self.assert_rejected(path, "round1", field, role)

    def test_next_round_requires_previous_verdict_to_be_fail(self):
        for text in (main_report(), main_report(问题="check.py:10 缺检查", 结论="改了就过")):
            with self.subTest(text=text):
                self.write_round(1, 1, None)
                self.write("group1/round1/main.txt", text)
                path = self.write_report("group1/round2/main.txt", main_report())
                self.assert_rejected(path, "round1", "不通过", "作者")

    def test_failed_previous_round_requires_author_handling(self):
        self.write_round(1, 1, "主审")
        (self.root / "group1/round1/handling.md").unlink()
        path = self.write_report("group1/round2/main.txt", main_report())
        self.assert_rejected(path, "round1/handling.md", "作者")

    def test_razor_failure_also_allows_next_round(self):
        self.write_round(1, 1, "剃刀")
        path = self.write_report("group1/round2/main.txt", main_report())
        self.assertEqual(self.run_check(path), (0, ""))

    def test_third_round_checks_both_earlier_rounds(self):
        for missing_round in (1, 2):
            with self.subTest(missing_round=missing_round):
                self.write_round(1, 1, "主审")
                self.write_round(1, 2, "主审")
                shutil.rmtree(self.root / f"group1/round{missing_round}")
                path = self.write_report("group1/round3/main.txt", main_report())
                self.assert_rejected(path, f"round{missing_round}", "主审", "剃刀")

    def test_third_round_checks_earlier_continuation_conditions(self):
        self.write_round(1, 1, None)
        self.write_round(1, 2, "主审")
        path = self.write_report("group1/round3/main.txt", main_report())
        self.assert_rejected(path, "round1", "不通过", "作者")

    def test_third_round_accepts_complete_history(self):
        self.write_round(1, 1, "主审")
        self.write_round(1, 2, "剃刀")
        path = self.write_report("group1/round3/main.txt", main_report())
        self.assertEqual(self.run_check(path), (0, ""))

    def test_previous_round_also_rejects_duplicate_roles(self):
        for text, role in ((main_report(), "主审"), (razor_report(), "剃刀")):
            with self.subTest(role=role):
                self.write_round(1, 1, "主审")
                extra = self.write("group1/round1/extra.txt", text)
                path = self.write_report("group1/round2/main.txt", main_report())
                self.assert_rejected(path, "round1", role, "extra.txt", "作者")
                extra.unlink()

    def test_next_group_requires_all_three_previous_rounds(self):
        for missing_round in (1, 2, 3):
            with self.subTest(missing_round=missing_round):
                self.write_failed_group(1)
                shutil.rmtree(self.root / f"group1/round{missing_round}")
                path = self.write_report("group2/round1/main.txt", main_report())
                self.assert_rejected(path, f"group1/round{missing_round}", "主审", "剃刀")

    def test_next_group_requires_both_roles_in_every_previous_round(self):
        for round_number in (1, 2, 3):
            for filename, role in (("main.txt", "主审"), ("razor.txt", "剃刀")):
                with self.subTest(round_number=round_number, role=role):
                    self.write_failed_group(1)
                    (self.root / f"group1/round{round_number}/{filename}").unlink()
                    path = self.write_report("group2/round1/main.txt", main_report())
                    self.assert_rejected(path, f"group1/round{round_number}", role)

    def test_next_group_requires_valid_previous_reports(self):
        self.write_failed_group(1)
        self.write("group1/round3/main.txt", main_report(本质=""))
        path = self.write_report("group2/round1/main.txt", main_report())
        self.assert_rejected(path, "group1/round3/main.txt", "本质", "主审")

    def test_next_group_requires_third_round_failure(self):
        self.write_failed_group(1)
        self.write_round(1, 3, None)
        path = self.write_report("group2/round1/main.txt", main_report())
        self.assert_rejected(path, "group1/round3", "不通过", "作者")

    def test_next_group_requires_third_round_handling(self):
        self.write_failed_group(1)
        (self.root / "group1/round3/handling.md").unlink()
        path = self.write_report("group2/round1/main.txt", main_report())
        self.assert_rejected(path, "group1/round3/handling.md", "作者")

    def test_next_group_requires_previous_diagnosis(self):
        self.write_failed_group(1).unlink()
        path = self.write_report("group2/round1/main.txt", main_report())
        self.assert_rejected(path, "group1/diagnosis.md", "作者", "判因子代理")

    def test_next_group_requires_valid_previous_diagnosis(self):
        self.write_failed_group(1)
        self.write("group1/diagnosis.md", diagnosis_report("继续审"))
        path = self.write_report("group2/round1/main.txt", main_report())
        self.assert_rejected(path, "group1/diagnosis.md", "结论", "判因子代理")

    def test_third_group_checks_first_group_diagnosis(self):
        self.write_failed_group(1).unlink()
        self.write_failed_group(2)
        path = self.write_report("group3/round1/main.txt", main_report())
        self.assert_rejected(path, "group1/diagnosis.md", "判因子代理")

    def test_later_group_accepts_complete_history(self):
        self.write_failed_group(1)
        self.write_failed_group(2)
        path = self.write_report("group3/round1/main.txt", main_report())
        self.assertEqual(self.run_check(path), (0, ""))

    def test_fourth_round_in_previous_group_also_blocks(self):
        self.write_failed_group(1)
        (self.root / "group1/round4").mkdir()
        path = self.write_report("group2/round1/main.txt", main_report())
        self.assert_rejected(path, "round4", "group1/diagnosis.md", "group2", "判因子代理")

    def test_diagnosis_requires_exactly_two_sections(self):
        valid = diagnosis_report("重想")
        for text, needle in ((valid.replace("## 判因\n", ""), "判因"),
                             (valid.replace("## 结论\n重想\n", ""), "结论"),
                             (valid + "## 备注\n补充\n", "备注"),
                             (valid + "## 判因\n重复\n", "出现了两次")):
            with self.subTest(text=text):
                path = self.write_failed_group(1)
                path.write_text(text, encoding="utf-8")
                self.assert_rejected(path, needle, "判因子代理")

    def test_diagnosis_requires_nonempty_reason(self):
        path = self.write_failed_group(1)
        path.write_text("## 判因\n\n## 结论\n重想\n", encoding="utf-8")
        self.assert_rejected(path, "判因", "为空", "判因子代理")

    def test_diagnosis_rejects_template_placeholder(self):
        path = self.write_failed_group(1)
        path.write_text("## 判因\n<这组为什么没过，引用各轮报告的阻断项和证据>\n## 结论\n重想\n", encoding="utf-8")
        self.assert_rejected(path, "占位符", "判因子代理")

    def test_diagnosis_requires_one_nonempty_action_line(self):
        for actions in ("", "重想\n顶回"):
            with self.subTest(actions=actions):
                path = self.write_failed_group(1)
                path.write_text(diagnosis_report(actions), encoding="utf-8")
                self.assert_rejected(path, "结论", "一行", "判因子代理")

    def test_diagnosis_rejects_unknown_actions(self):
        for actions in ("继续审", "拆分、继续审", "通过"):
            with self.subTest(actions=actions):
                path = self.write_failed_group(1)
                path.write_text(diagnosis_report(actions), encoding="utf-8")
                self.assert_rejected(path, "结论", "拆分", "重想", "问用户", "顶回", "判因子代理")

    def test_diagnosis_rejects_duplicate_actions(self):
        path = self.write_failed_group(1)
        path.write_text(diagnosis_report("拆分、重想、拆分"), encoding="utf-8")
        self.assert_rejected(path, "结论", "重复", "判因子代理")

    def test_diagnosis_requires_chinese_list_separator(self):
        for actions in ("拆分,重想", "拆分，重想", "拆分、", "、重想"):
            with self.subTest(actions=actions):
                path = self.write_failed_group(1)
                path.write_text(diagnosis_report(actions), encoding="utf-8")
                self.assert_rejected(path, "结论", "、", "判因子代理")

    def test_diagnosis_accepts_each_single_action(self):
        for action in ("拆分", "重想", "问用户", "顶回"):
            with self.subTest(action=action):
                path = self.write_failed_group(1)
                path.write_text(diagnosis_report(action), encoding="utf-8")
                self.assertEqual(self.run_check(path), (0, ""))

    def test_diagnosis_accepts_multiple_ordered_actions(self):
        for actions in ("问用户、拆分", "拆分、重想、问用户、顶回"):
            with self.subTest(actions=actions):
                path = self.write_failed_group(1)
                path.write_text(diagnosis_report(actions), encoding="utf-8")
                self.assertEqual(self.run_check(path), (0, ""))

    def test_diagnosis_requires_all_three_rounds(self):
        for missing_round in (1, 2, 3):
            with self.subTest(missing_round=missing_round):
                path = self.write_failed_group(1)
                shutil.rmtree(self.root / f"group1/round{missing_round}")
                self.assert_rejected(path, f"group1/round{missing_round}", "主审", "剃刀")

    def test_diagnosis_requires_both_roles(self):
        path = self.write_failed_group(1)
        (self.root / "group1/round3/razor.txt").unlink()
        self.assert_rejected(path, "group1/round3", "剃刀")

    def test_diagnosis_requires_valid_round_reports(self):
        path = self.write_failed_group(1)
        self.write("group1/round3/main.txt", main_report(本质=""))
        self.assert_rejected(path, "group1/round3/main.txt", "本质", "主审")

    def test_diagnosis_requires_third_round_failure(self):
        path = self.write_failed_group(1)
        self.write_round(1, 3, None)
        self.assert_rejected(path, "group1/round3", "不通过", "作者")

    def test_diagnosis_requires_third_round_handling(self):
        path = self.write_failed_group(1)
        (self.root / "group1/round3/handling.md").unlink()
        self.assert_rejected(path, "group1/round3/handling.md", "作者")

    def test_diagnosis_also_checks_all_earlier_groups(self):
        self.write_failed_group(1).unlink()
        path = self.write_failed_group(2)
        self.assert_rejected(path, "group1/diagnosis.md", "判因子代理")

    def test_current_round_requires_submitted_version(self):
        path = self.write_report("group1/round1/main.txt", main_report())
        (path.parent / "submitted-version.md").unlink()
        self.assert_rejected(path, "group1/round1/submitted", "作者")

    def test_next_round_requires_its_own_submitted_version(self):
        self.write_round(1, 1, "主审")
        path = self.write_report("group1/round2/main.txt", main_report())
        (path.parent / "submitted-version.md").unlink()
        self.assert_rejected(path, "group1/round2/submitted", "作者")

    def test_each_earlier_round_requires_submitted_version(self):
        for round_number in (1, 2):
            with self.subTest(round_number=round_number):
                self.write_round(1, 1, "主审")
                self.write_round(1, 2, "主审")
                (self.root / f"group1/round{round_number}/submitted-version.md").unlink()
                path = self.write_report("group1/round3/main.txt", main_report())
                self.assert_rejected(path, f"group1/round{round_number}/submitted", "作者")

    def test_each_previous_group_round_requires_submitted_version(self):
        for group in (1, 2):
            for round_number in (1, 2, 3):
                with self.subTest(group=group, round_number=round_number):
                    self.write_failed_group(1)
                    self.write_failed_group(2)
                    (self.root / f"group{group}/round{round_number}/submitted-version.md").unlink()
                    path = self.write_report("group3/round1/main.txt", main_report())
                    self.assert_rejected(path, f"group{group}/round{round_number}/submitted", "作者")

    def test_diagnosis_requires_submitted_version_for_all_three_rounds(self):
        for round_number in (1, 2, 3):
            with self.subTest(round_number=round_number):
                path = self.write_failed_group(1)
                (self.root / f"group1/round{round_number}/submitted-version.md").unlink()
                self.assert_rejected(path, f"group1/round{round_number}/submitted", "作者")

    def test_submitted_prefix_accepts_document_or_commit_record(self):
        path = self.write_report("group1/round1/main.txt", main_report())
        (path.parent / "submitted-version.md").unlink()
        for filename, text in (("submitted", "送审文档的原文\n"),
                               ("submitted-spec.md", "# 送审文档\n原文\n"),
                               ("submitted-code.txt", "d0348a4..123abcd\n")):
            with self.subTest(filename=filename):
                version = self.write(f"group1/round1/{filename}", text)
                self.assertEqual(self.run_check(path), (0, ""))
                version.unlink()

    def test_submitted_directory_does_not_replace_version_file(self):
        path = self.write_report("group1/round1/main.txt", main_report())
        (path.parent / "submitted-version.md").unlink()
        (path.parent / "submitted-spec.md").mkdir()
        self.assert_rejected(path, "group1/round1/submitted", "作者")

    def test_submitted_version_is_not_counted_as_review_report(self):
        path = self.write_report("group1/round1/main.txt", main_report())
        self.write("group1/round1/submitted-version.md", main_report())
        self.assertEqual(self.run_check(path), (0, ""))


if __name__ == "__main__":
    unittest.main()
