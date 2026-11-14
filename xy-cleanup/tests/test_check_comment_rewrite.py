import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "xy-cleanup" / "check_comment_rewrite.py"


class CommentRewriteCommandTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def write_file(self, name, content):
        path = self.directory / name
        path.write_bytes(content.encode("utf-8"))
        return path

    def run_command(self, *arguments):
        return subprocess.run(
            [sys.executable, "-B", str(COMMAND), *map(str, arguments)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def blocks(self, path):
        result = self.run_command("blocks", "--original", path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def splice(self, original, blocks, output=None):
        block_file = self.directory / "blocks.json"
        block_file.write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
        if output is None:
            output = self.directory / "rewrite" / original.name
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        result = self.run_command(
            "splice", "--original", original, "--blocks", block_file,
            "--output", output,
        )
        return result, Path(output)

    def gate(self, original, rewrite, judgement):
        judgement_path = self.directory / "judgement.json"
        if isinstance(judgement, str):
            judgement_path.write_text(judgement, encoding="utf-8")
        else:
            judgement_path.write_text(
                json.dumps(judgement, ensure_ascii=False), encoding="utf-8"
            )
        return self.run_command(
            "gate", "--original", original, "--rewrite", rewrite,
            "--judgement", judgement_path,
        )


class CommentBlockTest(CommentRewriteCommandTest):
    def test_python_docstrings_comments_and_ordinary_strings_are_classified(self):
        source = (
            '"""module docs"""\n'
            'VALUE = """ordinary string //"""\n'
            "# first\n# second\n"
            "value = 1  # trailing\n"
        )
        path = self.write_file("sample.py", source)

        self.assertEqual(
            self.blocks(path),
            [
                {"id": "1", "lines": "1-1", "text": '"""module docs"""'},
                {"id": "2", "lines": "3-4", "text": "# first\n# second"},
                {"id": "3", "lines": "5-5", "text": "# trailing"},
            ],
        )

    def test_rust_doc_comments_block_comments_and_string_contents_are_classified(self):
        source = (
            "/// public docs\n"
            "//! module docs\n"
            "/* block docs */\n"
            'let value = "// string";\n'
        )
        path = self.write_file("sample.rs", source)

        self.assertEqual(
            self.blocks(path),
            [
                {"id": "1", "lines": "1-1", "text": "/// public docs"},
                {"id": "2", "lines": "2-2", "text": "//! module docs"},
                {"id": "3", "lines": "3-3", "text": "/* block docs */"},
            ],
        )

    def test_c_preprocessor_directives_are_not_comment_blocks(self):
        path = self.write_file("sample.c", "#define SIZE 4\nint value; /* note */\n")

        self.assertEqual(
            self.blocks(path),
            [{"id": "1", "lines": "2-2", "text": "/* note */"}],
        )

    def test_shell_shebang_is_code_but_later_comments_are_blocks(self):
        path = self.write_file("sample.sh", "#!/bin/sh\n# note\necho ok\n")

        self.assertEqual(
            self.blocks(path),
            [{"id": "1", "lines": "2-2", "text": "# note"}],
        )

    def test_html_comments_are_blocks_but_doctype_is_not(self):
        path = self.write_file(
            "sample.html", "<!DOCTYPE html>\n<!-- note -->\n<div>ok</div>\n"
        )

        self.assertEqual(
            self.blocks(path),
            [{"id": "1", "lines": "2-2", "text": "<!-- note -->"}],
        )

    def test_yaml_comments_are_blocks(self):
        path = self.write_file("sample.yaml", "# note\nname: value\n")

        self.assertEqual(
            self.blocks(path),
            [{"id": "1", "lines": "1-1", "text": "# note"}],
        )

    def test_same_line_docstring_and_comment_have_distinct_block_ids(self):
        path = self.write_file(
            "sample.py", 'def f():\n    """docs"""  # trailing note\n'
        )

        self.assertEqual(
            self.blocks(path),
            [
                {"id": "1", "lines": "2-2", "text": '"""docs"""'},
                {"id": "2", "lines": "2-2", "text": "# trailing note"},
            ],
        )

    def test_unknown_language_exits_two(self):
        path = self.write_file("sample.unknown-language", "# note\n")

        result = self.run_command("blocks", "--original", path)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("不支持", result.stdout)
        self.assertEqual(result.stderr, "")


class CommentSpliceTest(CommentRewriteCommandTest):
    def test_replacement_preserves_every_byte_outside_comment_blocks(self):
        source = (
            "# a long note\r\n"
            "value =  1  # a trailing note\r\n"
            "other = 2\r\n"
            "# remove this full line\r\n"
        )
        original = self.write_file("sample.py", source)
        output = self.directory / "rewrite.py"
        manifest = [
            {"id": "1", "text": "# short"},
            {"id": "2", "text": "# tail"},
        ]

        result, output = self.splice(original, manifest, output)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            output.read_bytes(),
            b"# short\r\nvalue =  1  # tail\r\nother = 2\r\n# remove this full line\r\n",
        )

    def test_output_cannot_name_the_original_file(self):
        original = self.write_file("sample.py", "# note\n")

        result, _ = self.splice(
            original, [{"id": "1", "text": "# shorter"}], original
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("同一", result.stdout)
        self.assertEqual(original.read_text(encoding="utf-8"), "# note\n")

    def test_output_cannot_name_a_hard_link_to_the_original_file(self):
        original = self.write_file("sample.py", "# note\n")
        alias = self.directory / "alias.py"
        os.link(original, alias)

        result, _ = self.splice(
            original, [{"id": "1", "text": "# shorter"}], alias
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("同一", result.stdout)
        self.assertEqual(original.read_text(encoding="utf-8"), "# note\n")

    def test_same_line_docstring_and_comment_can_be_rewritten_independently(self):
        source = (
            'def f():\n'
            '    """long docstring"""  # long note\n'
            '    return 1\n'
        )
        original = self.write_file("sample.py", source)
        blocks = self.blocks(original)
        manifest = [
            {"id": blocks[0]["id"], "text": '"""short"""'},
            {"id": blocks[1]["id"], "text": "# note"},
        ]

        result, output = self.splice(
            original, manifest, self.directory / "rewrite.py"
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            output.read_text(encoding="utf-8"),
            'def f():\n    """short"""  # note\n    return 1\n',
        )

    def test_unknown_block_id_is_rejected(self):
        original = self.write_file("sample.py", "# first\n# second\n")

        result, output = self.splice(
            original, [{"id": "2", "text": "# shorter"}]
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("id", result.stdout)
        self.assertFalse(output.exists())

    def test_comment_block_cannot_be_rewritten_twice(self):
        original = self.write_file("sample.py", "# note\n")
        edit = {"id": "1", "text": "# short"}

        result, output = self.splice(original, [edit, edit])

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("重复", result.stdout)
        self.assertFalse(output.exists())

    def test_new_comment_text_cannot_terminate_a_comment_and_add_code(self):
        original = self.write_file("sample.c", "int f() { /* old */ return 1; }\n")

        result, output = self.splice(
            original, [{"id": "1", "text": "*/ x(); /*"}]
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("注释", result.stdout)
        self.assertFalse(output.exists())

    def test_deleting_full_line_comment_removes_line_and_inline_delete_keeps_spaces(self):
        original = self.write_file(
            "sample.c", "int value;   // trailing\nint middle;\n// full line\nint next;\n"
        )

        result, output = self.splice(
            original,
            [{"id": "1", "text": ""}, {"id": "2", "text": ""}],
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            output.read_text(encoding="utf-8"),
            "int value;   \nint middle;\nint next;\n",
        )

    def test_deleting_the_only_function_docstring_is_rejected(self):
        original = self.write_file("sample.py", 'def f():\n    """only docs"""\n')

        result, output = self.splice(
            original, [{"id": "1", "text": ""}]
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("文档串", result.stdout)
        self.assertFalse(output.exists())

    def test_tool_comment_cannot_be_changed(self):
        original = self.write_file("sample.py", "import unused  # noqa: F401\n")

        result, output = self.splice(
            original, [{"id": "1", "text": "# unused"}]
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("工具注释", result.stdout)
        self.assertFalse(output.exists())

    def test_fenced_code_example_in_docstring_must_remain_exact(self):
        source = (
            'def f():\n'
            '    """\n'
            '    ```python\n'
            "    print('old')\n"
            '    ```\n'
            '    """\n'
            '    return 1\n'
        )
        original = self.write_file("sample.py", source)
        changed = source.replace("print('old')", "print('new')")
        docstring = self.blocks(original)[0]["id"]

        result, output = self.splice(
            original, [{"id": docstring, "text": changed.split("    ", 1)[1].rstrip()}]
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("代码示例", result.stdout)
        self.assertFalse(output.exists())

    def test_doctest_lines_in_docstring_must_remain_exact(self):
        source = (
            'def f():\n'
            '    """\n'
            '    >>> f()\n'
            '    1\n'
            '    """\n'
            '    return 1\n'
        )
        original = self.write_file("sample.py", source)
        docstring = self.blocks(original)[0]["id"]
        replacement = '"""\n    >>> changed()\n    1\n    """'

        result, output = self.splice(
            original, [{"id": docstring, "text": replacement}]
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("代码示例", result.stdout)
        self.assertFalse(output.exists())


class CommentGateTest(CommentRewriteCommandTest):
    def setUp(self):
        super().setUp()
        self.original = self.write_file(
            "sample.py",
            "# original first\nvalue = 1\n# original second\n",
        )
        self.rewrite = self.write_file(
            "rewrite.py",
            "# first\nvalue = 1\n# second\n",
        )

    def valid_judgement(self):
        return {
            "inventory": [
                {
                    "id": "I1",
                    "block": "1",
                    "lines": "1-1",
                    "original": "# original first",
                    "status": "变了",
                    "rewritten": "# first",
                    "impact": "没影响",
                    "why": "压短重复描述",
                },
                {
                    "id": "I2",
                    "block": "2",
                    "lines": "3-3",
                    "original": "# original second",
                    "status": "变了",
                    "rewritten": "# second",
                    "impact": "没影响",
                    "why": "压短重复描述",
                },
            ],
            "added": [],
        }

    def test_complete_review_with_nonimpactful_rewrites_passes(self):
        result = self.gate(self.original, self.rewrite, self.valid_judgement())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")

    def test_inventory_must_cover_each_nonempty_line_of_each_block(self):
        source = self.write_file("multiline.py", "# first\n# second\nvalue = 1\n")
        rewrite = self.write_file("multiline-rewrite.py", "# first\n# second\nvalue = 1\n")
        judgement = {
            "inventory": [
                {
                    "id": "I1", "block": "1", "lines": "1-1",
                    "original": "# first", "status": "在", "rewritten": "# first",
                    "impact": "", "why": "",
                }
            ],
            "added": [],
        }

        result = self.gate(source, rewrite, judgement)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("第 2 行", result.stdout)

    def test_inventory_lines_cannot_extend_outside_the_assigned_block(self):
        judgement = self.valid_judgement()
        judgement["inventory"][0]["lines"] = "1-3"

        result = self.gate(self.original, self.rewrite, judgement)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("block", result.stdout)

    def test_inventory_original_must_be_found_in_its_assigned_block(self):
        judgement = self.valid_judgement()
        judgement["inventory"][0]["original"] = "# original second"

        result = self.gate(self.original, self.rewrite, judgement)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("original", result.stdout)

    def test_inventory_rewritten_must_be_found_in_rewrite_annotations(self):
        judgement = self.valid_judgement()
        judgement["inventory"][0]["rewritten"] = "# absent text"

        result = self.gate(self.original, self.rewrite, judgement)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("rewritten", result.stdout)

    def test_inventory_rewritten_cannot_be_assembled_across_separate_blocks(self):
        rewrite = self.write_file(
            "split-rewrite.py", "# ab\nvalue = 1\n# cd\n"
        )
        judgement = self.valid_judgement()
        judgement["inventory"][0]["rewritten"] = "ab# c"
        judgement["inventory"][1]["rewritten"] = "# cd"

        result = self.gate(self.original, rewrite, judgement)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("rewritten", result.stdout)

    def test_comment_blocks_cannot_be_omitted_from_inventory(self):
        result = self.gate(
            self.original, self.rewrite, {"inventory": [], "added": []}
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("block", result.stdout)

    def test_impactful_loss_is_rejected(self):
        judgement = self.valid_judgement()
        judgement["inventory"][0].update(
            status="丢了", rewritten="", impact="有影响", why="调用方依赖该约束"
        )

        result = self.gate(self.original, self.rewrite, judgement)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("有影响", result.stdout)

    def test_added_information_is_rejected(self):
        judgement = self.valid_judgement()
        judgement["added"].append({"text": "原文没有的新要求", "why": "猜测"})

        result = self.gate(self.original, self.rewrite, judgement)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("added", result.stdout)

    def test_inventory_ids_must_be_unique(self):
        judgement = self.valid_judgement()
        judgement["inventory"][1]["id"] = judgement["inventory"][0]["id"]

        result = self.gate(self.original, self.rewrite, judgement)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("重复", result.stdout)

    def test_review_requires_exact_root_and_inventory_fields(self):
        cases = []
        missing_root = self.valid_judgement()
        del missing_root["added"]
        cases.append(missing_root)
        extra_root = self.valid_judgement()
        extra_root["extra"] = True
        cases.append(extra_root)
        missing_item = self.valid_judgement()
        del missing_item["inventory"][0]["why"]
        cases.append(missing_item)
        extra_item = self.valid_judgement()
        extra_item["inventory"][0]["extra"] = True
        cases.append(extra_item)

        for judgement in cases:
            with self.subTest(judgement=judgement):
                result = self.gate(self.original, self.rewrite, judgement)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_duplicate_json_keys_are_rejected(self):
        result = self.gate(
            self.original, self.rewrite,
            '{"inventory":[],"inventory":[],"added":[]}',
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("重复键", result.stdout)

    def test_invalid_status_and_impact_values_are_rejected(self):
        for field, value in (("status", "maybe"), ("impact", "maybe")):
            with self.subTest(field=field):
                judgement = self.valid_judgement()
                judgement["inventory"][0][field] = value

                result = self.gate(self.original, self.rewrite, judgement)

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_same_line_blocks_are_reviewed_against_their_block_ids(self):
        source = self.write_file(
            "same-line.py", 'def f():\n    """docs"""  # note\n'
        )
        rewrite = self.write_file(
            "same-line-rewrite.py", 'def f():\n    """docs"""  # note\n'
        )
        judgement = {
            "inventory": [
                {
                    "id": "I1", "block": "1", "lines": "2-2",
                    "original": '"""docs"""', "status": "在",
                    "rewritten": '"""docs"""', "impact": "", "why": "",
                },
                {
                    "id": "I2", "block": "2", "lines": "2-2",
                    "original": "# note", "status": "在",
                    "rewritten": "# note", "impact": "", "why": "",
                },
            ],
            "added": [],
        }

        result = self.gate(source, rewrite, judgement)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_language_exits_two(self):
        source = self.write_file("sample.unknown-language", "# note\n")
        rewrite = self.write_file("rewrite.unknown-language", "# note\n")

        result = self.gate(source, rewrite, {"inventory": [], "added": []})

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("不支持", result.stdout)

class CommentRewriteDocumentationTest(unittest.TestCase):
    def setUp(self):
        self.root = ROOT

    def feature(self, value, feature_id):
        if value.get("id") == feature_id:
            return value
        for child in value.get("children", []):
            found = self.feature(child, feature_id)
            if found is not None:
                return found
        return None

    def test_feature_manifest_records_comment_rewrite_scope_and_files(self):
        document = json.loads(
            (self.root / "repo-map" / "features.json").read_text(encoding="utf-8")
        )
        cleanup = self.feature(document, "xy-cleanup")
        feature = self.feature(cleanup, "comment-rewrite")

        self.assertIsNotNone(feature)
        self.assertEqual(feature["title"], "改写代码注释")
        self.assertEqual(
            feature["what"],
            "清扫改完代码后，按块压短各语言代码文件和测试文件的注释和文档串："
            "只换注释块的文字，代码字节原样不动，给工具看的注释和注释里的代码示例不动；"
            "每份改写由新开的评审按完整信息清单核对，没有丢失对读代码改代码有影响的信息才直接用；"
            "认不出语言的文件不改。",
        )
        self.assertEqual(feature["why"]["source"], "确认")
        self.assertEqual(
            feature["why"]["from"],
            "/data/user/xy/runs/-home-xy-xy-skills/20261114-105306-xy-skills-code-comment-rewrite/xy-goal/quotes/15.md",
        )
        self.assertEqual(
            feature["entries"],
            [
                "xy-cleanup/check_comment_rewrite.py",
                "xy-cleanup/tests/test_check_comment_rewrite.py",
            ],
        )
        self.assertEqual(feature["items"], [])
        self.assertIn("改完代码后改写代码注释", cleanup["what"])

    def test_readme_names_pygments_and_install_command(self):
        readme = (self.root / "README.md").read_text(encoding="utf-8")

        self.assertIn("Pygments", readme)
        self.assertIn("python3 -m pip install Pygments", readme)

    def test_cleanup_rule_describes_scoping_rewrite_and_gate(self):
        rules = (self.root / "xy-cleanup" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("\n### 注释改写\n", rules)
        for required in (
            "注释改写", "功能直属的代码文件和测试文件", "repo-map/skip.json",
            "认不出语言", "check_comment_rewrite.py blocks", "块 id",
            "check_comment_rewrite.py splice", "check_comment_rewrite.py gate",
            "代码示例", "函数体只有一个文档串", "不进决定清单",
        ):
            with self.subTest(required=required):
                self.assertIn(required, rules, f"缺少规则：{required}")


if __name__ == "__main__":
    unittest.main()
