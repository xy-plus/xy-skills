import pathlib
import subprocess
import sys
import tempfile
import unittest

CHECK = pathlib.Path(__file__).with_name("check.py")

VALID = """## 第一性原理
查了：是
多做：无
少做：无
补丁：无
结论：通过

## 难误用
查了：是
问题：无
结论：通过

## 奥卡姆剃刀
查了：是
删除清单：
- 删 foo()：没有调用方，删了不丢东西
旧代码删减：无
结论：不通过

## 仓库规范
查了：是
问题：无
结论：通过
"""


def run(text):
    with tempfile.TemporaryDirectory() as tmp:
        report = pathlib.Path(tmp) / "review.md"
        report.write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, str(CHECK), str(report)],
                              capture_output=True, text=True)


class CheckTest(unittest.TestCase):
    def test_valid_report_passes_and_prints_conclusions(self):
        result = run(VALID)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("奥卡姆剃刀：不通过", result.stdout)

    def test_field_name_may_carry_a_parenthetical_note(self):
        result = run(VALID.replace("删除清单：", "删除清单（共 1 条）："))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_section_fails(self):
        result = run(VALID.replace("## 难误用", "## 别的"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("缺少「## 难误用」", result.stdout)

    def test_missing_checked_line_fails(self):
        text = VALID.replace("## 仓库规范\n查了：是", "## 仓库规范")
        result = run(text)
        self.assertEqual(result.returncode, 1)
        self.assertIn("仓库规范：缺少「查了：是」", result.stdout)

    def test_empty_field_fails(self):
        result = run(VALID.replace("旧代码删减：无", "旧代码删减："))
        self.assertEqual(result.returncode, 1)
        self.assertIn("奥卡姆剃刀：「旧代码删减」为空", result.stdout)

    def test_bad_conclusion_fails(self):
        result = run(VALID.replace("问题：无\n结论：通过\n\n## 奥卡姆",
                                   "问题：无\n结论：基本通过\n\n## 奥卡姆"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("难误用：结论必须是「通过」或「不通过」", result.stdout)


if __name__ == "__main__":
    unittest.main()
