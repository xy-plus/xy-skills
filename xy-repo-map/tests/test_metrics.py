"""Four-number and public documentation coverage tests."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_map_lib import constants
from repo_map_lib.metrics import LineCounts, code_counts, count_file, coverage_counts


class MetricsTest(unittest.TestCase):
    def test_code_suffixes_have_one_registry(self):
        self.assertFalse(hasattr(constants, "SUPPORTED_CODE_SUFFIXES"))

    def test_line_counts_leave_aggregation_to_the_rendering_layer(self):
        self.assertNotIn("__add__", LineCounts.__dict__)

    def test_line_count_record_requires_all_categories_explicitly(self):
        with self.assertRaises(TypeError):
            LineCounts()

    def test_python_docstrings_comments_inline_comments_and_public_coverage(self):
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
        self.assertEqual(coverage_counts("sample.py", source), (2, 3, "Python"))

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
        self.assertEqual(coverage_counts("lib.rs", source), (1, 1, "Rust"))

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
        self.assertEqual(coverage_counts("file.mjs", js_source), (0, 0, "不适用"))
        self.assertEqual(coverage_counts("script.sh", "echo hi\n"), (0, 0, "不适用"))
        self.assertEqual(coverage_counts("header.h", "int value;\n"), (0, 0, "不适用"))
        self.assertEqual(coverage_counts("file.tsx", "export const x = 1;\n"),
                         (0, 0, "不适用"))

    def test_docs_and_tests_count_nonempty_lines_and_binary_files_count_zero(self):
        docs = count_file("guide.md", "docs", b"text\n\n next \n")
        tests = count_file("test.py", "tests", b"one\n\nnext\n")
        binary = count_file("data.bin", "code", b"\x00\xff\x01")
        self.assertEqual((docs.code, docs.docs, docs.comments, docs.tests), (0, 2, 0, 0))
        self.assertEqual((tests.code, tests.docs, tests.comments, tests.tests), (0, 0, 0, 2))
        self.assertEqual((binary.code, binary.docs, binary.comments, binary.tests), (0, 0, 0, 0))

    def test_unsupported_code_suffix_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不支持"):
            code_counts("source.go", "package main\n")


if __name__ == "__main__":
    unittest.main()
