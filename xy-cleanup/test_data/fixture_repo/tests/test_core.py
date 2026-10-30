"""Tests for the sample core behavior."""
import unittest

from cleanup_fixture.core import normalize_label


class CoreTest(unittest.TestCase):
    def test_normalize_label(self):
        self.assertEqual(normalize_label("  Hello   World "), "hello world")

    def test_clean_label(self):
        self.assertEqual(normalize_label("  Hello   World "), "hello world")


if __name__ == "__main__":
    unittest.main()
