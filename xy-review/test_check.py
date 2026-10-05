"""check.py 的测试，一个用例守一条规则。运行：PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest test_check"""
import contextlib
import io
import os
import tempfile
import unittest

from check import check, main


def report(**overrides):
    fields = {
        "本质": "用户要的是 X",
        "偏离": "无，对照了 spec 第 1、2 条",
        "问题": "无，试了两种用错的写法都当场报错",
        "留下清单": "参数 x：原话 1 要求可配置",
        "删除清单": "无，逐样问过",
        "旧代码删减": "无，没碰到旧代码",
        "规范": "\n".join(f"{i}. 无，查了 Y" for i in range(1, 8)),
        "结论": "通过",
    }
    fields.update(overrides)
    return (
        "## 铁律 1 第一性原理\n"
        f"本质：{fields['本质']}\n偏离：{fields['偏离']}\n"
        "## 铁律 2 难误用\n"
        f"问题：{fields['问题']}\n"
        "## 铁律 3 奥卡姆剃刀\n"
        f"留下清单：{fields['留下清单']}\n删除清单：{fields['删除清单']}\n旧代码删减：{fields['旧代码删减']}\n"
        "## 仓库规范\n"
        f"{fields['规范']}\n"
        "## 结论\n"
        f"{fields['结论']}\n"
    )


class CheckTest(unittest.TestCase):
    def assertProblems(self, text, *needles):
        problems, _ = check(text)
        self.assertTrue(problems, "应该报错却通过了")
        for needle in needles:
            self.assertTrue(any(needle in p for p in problems), f"没报「{needle}」：{problems}")

    def test_clean_pass(self):
        self.assertEqual(check(report()), ([], "通过"))

    def test_fail_with_findings(self):
        text = report(偏离="a.py:3 没改根源，在 b.py 兜底",
                      删除清单="- 参数 x；删了丢什么：无\n- 分支 y；删了丢什么：无",
                      结论="不通过")
        self.assertEqual(check(text), ([], "不通过"))

    def test_missing_section(self):
        self.assertProblems(report().replace("## 铁律 2 难误用\n", ""), "缺少「## 铁律 2 难误用」")

    def test_unknown_section(self):
        self.assertProblems(report() + "## 备注\n随便写\n", "不认识的节")

    def test_duplicate_section(self):
        text = report().replace("## 铁律 2 难误用\n", "## 铁律 2 难误用\n问题：check.py:100 漏检\n## 铁律 2 难误用\n")
        self.assertProblems(text, "「## 铁律 2 难误用」出现了两次")

    def test_empty_field(self):
        self.assertProblems(report(偏离=""), "「偏离」为空")

    def test_missing_field(self):
        self.assertProblems(report().replace("偏离：无，对照了 spec 第 1、2 条\n", ""), "缺少「偏离」")

    def test_duplicate_field(self):
        self.assertProblems(report(问题="check.py:100 漏检\n问题：无，查了"), "「问题」出现了两次")

    def test_nonstandard_field_line(self):
        self.assertProblems(report(问题="无，核过\n问题: a.py:10 仍漏检"), "全角冒号")
        self.assertProblems(report(问题="无，核过\n**问题**：a.py:10 仍漏检"), "全角冒号")
        self.assertProblems(report().replace("本质：用户要的是 X", "本质: 用户要的是 X"), "全角冒号", "缺少「本质」")

    def test_list_item_named_like_a_field_is_content(self):
        self.assertEqual(check(report(留下清单="\n- 删除清单：原话 8 要求说明删了丢什么")), ([], "通过"))
        self.assertEqual(check(report(留下清单="- 格式脚本：原话 7 要求格式检查\n- 删除清单：原话 8 要求说明删了丢什么")), ([], "通过"))

    def test_placeholder_whole_field(self):
        self.assertProblems(report(本质="<用户真正要的是什么；若是修错，根源在哪>"), "占位符")

    def test_placeholder_inside_line(self):
        self.assertProblems(report(留下清单="参数 x：<离开它，用户的哪个要求做不到>"), "占位符")

    def test_bare_none_needs_comparison(self):
        self.assertProblems(report(偏离="无"), "后面要写对照了什么")
        self.assertProblems(report(偏离="无（）"), "后面要写对照了什么")

    def test_none_must_be_single_line(self):
        self.assertProblems(report(删除清单="无，逐样问过\n参数 x；删了丢什么：无"), "只能有这一行")
        self.assertProblems(report(偏离="无，对照了 X\ncheck.py:12 没改根源"), "只能有这一行")

    def test_none_with_parentheses_or_bullet_is_none(self):
        self.assertEqual(check(report(偏离="无（对照了 spec）")), ([], "通过"))
        self.assertEqual(check(report(偏离="\n- 无，对照了 spec")), ([], "通过"))

    def test_word_starting_with_none_is_a_finding(self):
        self.assertProblems(report(问题="无法拒绝空路径，check.py:138 直接打开"),
                            "结论写通过，但有的地方不是「无，」开头：「问题」（无法拒绝空路径")

    def test_rule_missing(self):
        self.assertProblems(report(规范="1. 无，查了\n…\n7. 无，查了"), "第 2 条缺失", "第 6 条缺失")

    def test_rule_duplicate(self):
        text = report(规范="1. check.py:100 漏检\n1. 无，查了\n" + "\n".join(f"{i}. 无，查了" for i in range(2, 8)))
        self.assertProblems(text, "第 1 条出现了两次")

    def test_rule_out_of_range(self):
        self.assertProblems(report(规范="\n".join(f"{i}. 无，查了" for i in range(1, 9))), "只有 1 到 7 条")

    def test_conclusion_single_line(self):
        self.assertProblems(report(结论="通过；删除清单 0 条"), "结论只能有一行")
        self.assertProblems(report(结论="通过\n### Assessment\nReady"), "结论只能有一行")

    def test_pass_with_findings(self):
        self.assertProblems(report(问题="缺省值静默生效"), "结论写通过")

    def test_fix_and_pass_needs_findings(self):
        self.assertEqual(check(report(问题="缺省值静默生效", 结论="改了就过")), ([], "改了就过"))
        self.assertProblems(report(结论="改了就过"), "结论写改了就过，但各节都是「无」")

    def test_fail_without_findings(self):
        self.assertProblems(report(结论="不通过"), "结论写不通过")

    def test_keep_line_without_reason(self):
        self.assertProblems(report(留下清单="参数 x：原话 1 要求可配置\n分支 y"), "留下清单每行")

    def test_delete_line_without_loss(self):
        self.assertProblems(report(删除清单="参数 x", 结论="不通过"), "删除清单每行")

    def test_list_marker_is_not_an_entity(self):
        self.assertProblems(report(留下清单="- ：用户要求必须保留"), "留下清单每行")
        self.assertProblems(report(删除清单="- ；删了不丢要求", 结论="不通过"), "删除清单每行")

    def test_old_code_does_not_block(self):
        self.assertEqual(check(report(旧代码删减="old.py:10 死分支")), ([], "通过"))

    def test_exit_codes(self):
        cases = [(report(), 0), (report(问题="缺省值静默生效", 结论="改了就过"), 3),
                 (report(问题="缺省值静默生效", 结论="不通过"), 4), (report(偏离=""), 1)]
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
