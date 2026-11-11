"""Tests for exact citations to numbered repository rules."""
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
        self.assertIsNone(rule_quote_problem("xy-iron-law 第 1 条", "第一性原理：思考"))
        problem = rule_quote_problem("xy-iron-law 第 1 条", "第一性原理……思考")
        self.assertIsInstance(problem, str)

    def test_rule_document_without_numbered_rules_raises(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill_path = Path(temporary) / "rules.md"
            skill_path.write_text("没有编号条款。\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                numbered_rule_numbers(skill_path)

    def test_numbered_rules_read_each_document_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill_path = Path(temporary) / "rules.md"
            skill_path.write_text("1. 第一条\n正文。\n2. 第二条\n结束。\n", encoding="utf-8")
            original_read_text = Path.read_text
            reads = []

            def count_rule_read(path, *args, **kwargs):
                if path == skill_path:
                    reads.append(path)
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", count_rule_read):
                numbered_text(skill_path, 1)
                numbered_text(skill_path, 2)
            self.assertEqual(reads, [skill_path])
