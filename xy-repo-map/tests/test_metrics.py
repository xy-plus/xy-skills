"""Test code, documentation, comment, and test line counts."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib.metrics import LineCounts, code_counts, count_file


class MetricsTest(unittest.TestCase):
    def test_line_count_record_requires_all_categories_explicitly(self):
        with self.assertRaises(TypeError):
            LineCounts()

    def test_python_docstrings_comments_and_inline_comments(self):
        source = '''"""Module docs.
Second docs line.
"""

# ordinary comment
value = 1  # trailing comment is code

class Public:
    """Public class docs."""

    def method(self):
        """Method docs."""
        return value

    def _private(self):
        return 0

def public_function():
    return value
'''
        counts = code_counts("sample.py", source)
        self.assertEqual((counts.code, counts.docs, counts.comments), (8, 5, 1))

    def test_shell_shebang_is_code_but_full_line_hash_is_comment(self):
        counts = code_counts("script.sh", "#!/bin/sh\n# explanation\necho hi # inline\n\n")
        self.assertEqual((counts.code, counts.docs, counts.comments), (2, 0, 1))

    def test_rust_doc_comments_and_nested_block_comments_are_separate(self):
        source = '''//! Crate docs.
/// Public API docs.
pub fn visible() {}
pub(crate) fn hidden() {}
/* normal start
   /* nested */
   normal end */
let answer = 42; // inline
'''
        counts = code_counts("lib.rs", source)
        self.assertEqual((counts.code, counts.docs, counts.comments), (3, 2, 3))

    def test_rust_raw_string_bodies_do_not_turn_slashes_into_comments(self):
        samples = (
            'let value = r"first line\n// still raw text\n";\n',
            'let value = r#"an " quote\n// still raw text\n"#;\n',
        )

        for source in samples:
            with self.subTest(source=source):
                counts = code_counts("raw.rs", source)
                self.assertEqual((counts.code, counts.docs, counts.comments), (3, 0, 0))

    def test_template_interpolation_comments_follow_code_rules_through_nesting(self):
        samples = (
            ("const text = `value ${value\n  // interpolation comment\n  + 1\n}`;\n",
             (3, 0, 1)),
            ("const text = `outer ${`inner ${1\n  // nested comment\n  + 2}`\n"
             "  // outer interpolation comment\n}`;\n", (3, 0, 2)),
        )

        for source, expected in samples:
            with self.subTest(source=source):
                counts = code_counts("template.ts", source)
                self.assertEqual((counts.code, counts.docs, counts.comments), expected)

    def test_c_and_javascript_doc_blocks_count_as_documentation(self):
        c_source = "/** API docs\n * continued\n */\nint value;\n/* note */\n"
        c_counts = code_counts("file.c", c_source)
        self.assertEqual((c_counts.code, c_counts.docs, c_counts.comments), (1, 3, 1))

        js_source = "/** docs */\nexport function run() {}\n// note\nconst x = 1;\n"
        js_counts = code_counts("file.mjs", js_source)
        self.assertEqual((js_counts.code, js_counts.docs, js_counts.comments), (2, 1, 1))

    def test_count_file_counts_the_four_file_categories(self):
        code = count_file("sample.py", "code", "value = 1\n")
        docs = count_file("guide.md", "docs", "text\n\n next \n")
        tests = count_file("test.py", "tests", "one\n\nnext\n")
        other = count_file("data.json", "other", '{"value": 1}\n')
        self.assertEqual((code.code, code.docs, code.comments, code.tests), (1, 0, 0, 0))
        self.assertEqual((docs.code, docs.docs, docs.comments, docs.tests), (0, 2, 0, 0))
        self.assertEqual((tests.code, tests.docs, tests.comments, tests.tests), (0, 0, 0, 2))
        self.assertEqual((other.code, other.docs, other.comments, other.tests), (0, 0, 0, 0))

    def test_count_file_requires_text_content(self):
        with self.assertRaisesRegex(TypeError, "文本"):
            count_file("guide.md", "docs", b"text\n")

    def test_count_file_rejects_an_unknown_category(self):
        with self.assertRaisesRegex(ValueError, "文件类别"):
            count_file("file.bin", "unknown", "text\n")

    def test_unsupported_code_suffix_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不支持"):
            code_counts("source.go", "package main\n")


if __name__ == "__main__":
    unittest.main()
