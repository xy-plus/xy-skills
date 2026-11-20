"""Validate the decision document shape and comparison ranges."""

import copy
from pathlib import Path
import sys
import unittest

ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
DECISION_PAGE_DIRECTORY = ROOT / "repository-care" / "xy-cleanup" / "decision_page"
sys.path.insert(0, str(DECISION_PAGE_DIRECTORY))
import decision_page  # noqa: E402
from test_decision_page import valid_decisions  # noqa: E402


class DecisionShapeValidationTest(unittest.TestCase):
    def test_each_object_level_requires_exact_fields(self):
        with_footnote = valid_decisions()
        with_footnote["footnotes"] = [
            {"title": "Keep in mind", "lines": ["No action needed."]}
        ]
        cases = []

        missing_top = valid_decisions()
        del missing_top["intro"]
        cases.append((missing_top, "intro"))
        missing_group = valid_decisions()
        del missing_group["groups"][0]["notes"]
        cases.append((missing_group, "notes"))
        missing_item = valid_decisions()
        del missing_item["groups"][0]["items"][0]["lines"]
        cases.append((missing_item, "lines"))
        missing_footnote = copy.deepcopy(with_footnote)
        del missing_footnote["footnotes"][0]["title"]
        cases.append((missing_footnote, "title"))
        extra_footnote = copy.deepcopy(with_footnote)
        extra_footnote["footnotes"][0]["legacy"] = True
        cases.append((extra_footnote, "legacy"))
        missing_compare = valid_decisions()
        del missing_compare["groups"][0]["items"][0]["compare"]
        cases.append((missing_compare, "compare"))
        missing_details = valid_decisions()
        del missing_details["groups"][0]["items"][0]["details"]
        cases.append((missing_details, "details"))

        for decisions, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError) as context:
                    decision_page.validate_decisions(decisions)
                self.assertIn(expected, str(context.exception))

    def test_details_accepts_null_and_a_complete_object(self):
        null_details = valid_decisions()
        self.assertIs(decision_page.validate_decisions(null_details), null_details)

        decision = valid_decisions()
        decision["groups"][0]["items"][0]["details"] = {
            "title": "起草条目 2 条",
            "lines": ["alpha：第一条。", "beta：第二条。"],
        }
        self.assertIs(decision_page.validate_decisions(decision), decision)

    def test_details_rejects_missing_extra_and_invalid_fields(self):
        cases = (
            ("extra", {"title": "更多", "lines": ["条目。"], "extra": True}, "extra"),
            ("non-object", ["条目。"], "details"),
            ("missing-title", {"lines": ["条目。"]}, "title"),
            ("empty-title", {"title": " ", "lines": ["条目。"]}, "title"),
            ("non-string-title", {"title": 7, "lines": ["条目。"]}, "title"),
            ("missing-lines", {"title": "更多"}, "lines"),
            ("non-array-lines", {"title": "更多", "lines": "条目。"}, "lines"),
            ("empty-lines", {"title": "更多", "lines": []}, "lines"),
            ("empty-line", {"title": "更多", "lines": [""]}, "lines[0]"),
            ("non-string-line", {"title": "更多", "lines": [7]}, "lines[0]"),
        )
        for label, details, expected in cases:
            with self.subTest(label=label):
                decision = valid_decisions()
                decision["groups"][0]["items"][0]["details"] = details
                with self.assertRaises(ValueError) as context:
                    decision_page.validate_decisions(decision)
                self.assertIn(expected, str(context.exception))

    def test_compare_accepts_null_and_block_array_with_an_empty_after(self):
        null_compare = valid_decisions()
        decision_page.validate_decisions(null_compare)

        deleted_compare = valid_decisions()
        deleted_compare["groups"][0]["items"][0]["compare"] = [
            {"lines": "guide.md:1-1", "before": "A paragraph to remove.", "after": ""},
        ]
        self.assertIs(
            decision_page.validate_decisions(deleted_compare), deleted_compare
        )

    def test_compare_rejects_invalid_shapes_and_ranges(self):
        cases = (
            ("not an array", "compare 必须是非空数组或 null"),
            ([], "compare 必须是非空数组或 null"),
            ({"before": "Before.", "after": "After."}, "compare 必须是非空数组或 null"),
            ([{"before": "Before.", "after": "After."}], "缺少字段 lines"),
            ([{"lines": "guide.md:1-1", "before": "Before."}], "缺少字段 after"),
            (
                [
                    {
                        "lines": "guide.md:1-1",
                        "before": "Before.",
                        "after": "After.",
                        "extra": 1,
                    }
                ],
                "多余字段 extra",
            ),
            (
                [{"lines": "guide.md:1-1", "before": "", "after": "After."}],
                "before 必须是非空字符串",
            ),
            (
                [{"lines": "guide.md:1-1", "before": 7, "after": "After."}],
                "before 必须是非空字符串",
            ),
            (
                [{"lines": "guide.md:1-1", "before": "Before.", "after": 7}],
                "after 必须是字符串",
            ),
            ([None], "必须是对象"),
            (
                [{"lines": "guide.md:1", "before": "Before.", "after": "After."}],
                "行范围",
            ),
            (
                [{"lines": "guide.md:0-1", "before": "Before.", "after": "After."}],
                "行范围",
            ),
            (
                [{"lines": "guide.md:2-1", "before": "Before.", "after": "After."}],
                "行范围",
            ),
            (
                [
                    {"lines": "guide.md:1-1", "before": "First.", "after": "First."},
                    {"lines": "other.md:2-2", "before": "Second.", "after": "Second."},
                ],
                "同一个路径",
            ),
            (
                [
                    {"lines": "guide.md:2-2", "before": "First.", "after": "First."},
                    {"lines": "guide.md:1-1", "before": "Second.", "after": "Second."},
                ],
                "起始行递增",
            ),
            (
                [
                    {"lines": "guide.md:1-3", "before": "First.", "after": "First."},
                    {"lines": "guide.md:3-4", "before": "Second.", "after": "Second."},
                ],
                "不能重叠",
            ),
        )
        for compare, expected in cases:
            with self.subTest(compare=compare):
                decisions = valid_decisions()
                decisions["groups"][0]["items"][0]["compare"] = compare
                with self.assertRaises(ValueError) as context:
                    decision_page.validate_decisions(decisions)
                self.assertIn(expected, str(context.exception))

    def test_text_fields_are_nonempty_strings_at_every_level(self):
        cases = []
        for field, value, expected in (
            ("title", " ", "title"),
            ("intro", [""], "intro[0]"),
        ):
            decisions = valid_decisions()
            decisions[field] = value
            cases.append((decisions, expected))
        for section, field, value, expected in (
            ("group", "title", "", "groups[0].title"),
            ("group", "notes", [" "], "groups[0].notes[0]"),
            ("item", "id", "", "groups[0].items[0].id"),
            ("item", "title", "", "groups[0].items[0].title"),
            ("item", "lines", [""], "groups[0].items[0].lines[0]"),
            ("item", "options", ["Keep", " "], "groups[0].items[0].options[1]"),
            ("item", "recommended", " ", "groups[0].items[0].recommended"),
            ("footnote", "title", "", "footnotes[0].title"),
            ("footnote", "lines", [""], "footnotes[0].lines[0]"),
        ):
            decisions = valid_decisions()
            if section == "group":
                decisions["groups"][0][field] = value
            elif section == "item":
                decisions["groups"][0]["items"][0][field] = value
            else:
                decisions["footnotes"] = [
                    {"title": "Keep in mind", "lines": ["No action needed."]}
                ]
                decisions["footnotes"][0][field] = value
            cases.append((decisions, expected))

        for decisions, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError) as context:
                    decision_page.validate_decisions(decisions)
                self.assertIn(expected, str(context.exception))

    def test_arrays_must_have_their_documented_types(self):
        cases = []
        for key in ("intro", "groups", "footnotes"):
            decisions = valid_decisions()
            decisions[key] = "not an array"
            cases.append((decisions, key))
        invalid_group = valid_decisions()
        invalid_group["groups"][0]["notes"] = "not an array"
        cases.append((invalid_group, "notes"))
        invalid_items = valid_decisions()
        invalid_items["groups"][0]["items"] = "not an array"
        cases.append((invalid_items, "items"))
        invalid_lines = valid_decisions()
        invalid_lines["groups"][0]["items"][0]["lines"] = "not an array"
        cases.append((invalid_lines, "lines"))
        invalid_options = valid_decisions()
        invalid_options["groups"][0]["items"][0]["options"] = "not an array"
        cases.append((invalid_options, "options"))

        for decisions, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError) as context:
                    decision_page.validate_decisions(decisions)
                self.assertIn(expected, str(context.exception))
