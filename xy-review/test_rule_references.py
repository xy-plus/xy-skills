from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rule_references import (RULE_FILES, numbered_rule_numbers, numbered_text,
                             rule_quote_problem)


class RuleReferencesTest(unittest.TestCase):
    def test_rule_number_is_read_from_the_numbered_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill_path = Path(temporary) / "rules.md"
            skill_path.write_text(
                "1. 第一条\n正文。\n4. 第四条\n结束。\n",
                encoding="utf-8",
            )
            self.assertEqual(numbered_text(skill_path, 4), "4. 第四条\n结束。")
            self.assertEqual(numbered_text(skill_path, 5), "")
            self.assertEqual(numbered_rule_numbers(skill_path), (1, 4))

    def test_rule_citation_rejects_out_of_range_number_from_its_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill_path = Path(temporary) / "rules.md"
            skill_path.write_text("1. 第一条\n正文。\n4. 第四条\n结束。\n", encoding="utf-8")
            with patch.dict(RULE_FILES, {"xy-iron-law": skill_path}):
                self.assertIsNone(rule_quote_problem("xy-iron-law 第 4 条", "第四条"))
                problem = rule_quote_problem("xy-iron-law 第 5 条", "第五条")
            self.assertIsInstance(problem, str)

    def test_rule_quote_must_be_continuous_text_from_its_named_rule(self):
        problem = rule_quote_problem("xy-iron-law 第 1 条", "第一性原理……思考")
        self.assertIsInstance(problem, str)

    def test_rule_document_without_numbered_rules_raises(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill_path = Path(temporary) / "rules.md"
            skill_path.write_text("没有编号条款。\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                numbered_rule_numbers(skill_path)
