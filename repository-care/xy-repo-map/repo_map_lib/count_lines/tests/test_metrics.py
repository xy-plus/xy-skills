from pathlib import Path
import sys
import unittest

REPOSITORY_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists())
sys.path.insert(0, str(REPOSITORY_ROOT / "repository-care" / "xy-repo-map"))

from repo_map_lib.count_lines.metrics import count_file


class MetricsTest(unittest.TestCase):
    def test_python_and_rust_doc_comments_are_documentation(self):
        cases = (
            (
                "sample.py",
                '''"""Module docs.
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
''',
                (8, 5, 1),
            ),
            (
                "lib.rs",
                '''//! Crate docs.
/// Public API docs.
/* normal start
   /* nested */
   normal end */
let answer = 42; // inline
''',
                (1, 2, 3),
            ),
        )
        for path, source, expected in cases:
            with self.subTest(path=path):
                counts = count_file(path, "code", source)
                self.assertEqual((counts.code, counts.docs, counts.comments), expected)

    def test_python_docstrings_inside_control_flow_blocks_are_documentation(self):
        source = '''if condition:
    def nested_function():
        """Function docs."""
    class NestedClass:
        """Class docs."""
'''
        counts = count_file("sample.py", "code", source)
        self.assertEqual(counts.docs, 2)

    def test_shell_shebang_is_code_but_full_line_hash_is_comment(self):
        counts = count_file("script.sh", "code", "#!/bin/sh\n# explanation\necho hi # inline\n\n")
        self.assertEqual((counts.code, counts.docs, counts.comments), (2, 0, 1))

    def test_rust_string_bodies_do_not_turn_slashes_into_comments(self):
        samples = (
            'let value = r"first line\n// still raw text\n";\n',
            'let value = r#"an " quote\n// still raw text\n"#;\n',
            'let value = "first line\n// still string text\n";\n',
        )

        for source in samples:
            counts = count_file("raw.rs", "code", source)
            self.assertEqual((counts.code, counts.docs, counts.comments), (3, 0, 0))

    def test_c_doc_blocks_count_as_documentation(self):
        c_source = "/** API docs\n * continued\n */\nint value;\n/* note */\n"
        c_counts = count_file("file.c", "code", c_source)
        self.assertEqual((c_counts.code, c_counts.docs, c_counts.comments), (1, 3, 1))
