"""The cleanup instructions expose the required workflow and report contract."""
from pathlib import Path
import unittest


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class CleanupSkillTest(unittest.TestCase):
    def test_skill_exists_and_describes_the_operational_flow(self):
        self.assertTrue(SKILL.is_file())
        text = SKILL.read_text(encoding="utf-8")
        for phrase in (
            "什么时候用", "仓根的绝对路径", "节点 FQN", "基线", "机械旁证",
            "引用对账", "全图", "主审", "剃刀", "check_audit.py",
            "只问一次", "xy-workflow", "四个数", "references.py",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_skill_contains_no_spec_or_quote_identifiers_or_round_history(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\bR[0-9]+\b")
        self.assertNotRegex(text, r"原话\s*[0-9]+")
        self.assertNotRegex(text, r"round\s*[0-9]+")

    def test_root_readme_lists_the_skill_and_test_command(self):
        readme = SKILL.parents[1] / "README.md"
        text = readme.read_text(encoding="utf-8")
        self.assertIn("`xy-cleanup`", text)
        self.assertIn("python3 -B -m unittest discover -s xy-cleanup/tests", text)


if __name__ == "__main__":
    unittest.main()
