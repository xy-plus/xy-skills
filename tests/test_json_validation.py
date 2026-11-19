import sys
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
from json_validation import check_value, object_without_duplicate_keys  # noqa: E402


class JsonValidationTest(unittest.TestCase):
    def test_duplicate_object_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "重复键：value"):
            object_without_duplicate_keys([("value", 1), ("value", 2)])

    def test_nested_shape_problems_include_their_path(self):
        problems = check_value(
            "报告",
            {"items": [{"name": "", "extra": "x"}]},
            {"items": [{"name": str}]},
        )

        self.assertEqual(problems, [
            "报告.items[0] 多余字段：extra",
            "报告.items[0].name 不能为空",
        ])
