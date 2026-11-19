from pathlib import Path
import sys
import unittest


REPOSITORY_CARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_CARE))
from text_metrics import count_non_whitespace_characters  # noqa: E402


class TextMetricsTest(unittest.TestCase):
    def test_counts_non_whitespace_characters(self):
        self.assertEqual(count_non_whitespace_characters("你 好\nA\tB"), 4)
