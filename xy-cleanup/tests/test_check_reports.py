"""Test the public validator for per-feature cleanup review reports."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "xy-repo-map" / "tests"))
sys.path.insert(0, str(ROOT / "xy-cleanup"))

from map_fixture import feature, make_repo  # noqa: E402
import check_reports as check_reports_module  # noqa: E402


COMMAND = ROOT / "xy-cleanup" / "check_reports.py"
RULE_SOURCE = "xy-iron-law 第 3 条"
RULE_TEXT = "如无必要，勿增实体（奥卡姆剃刀）。"


def razor_keep_item(feature_id, lines=None, source="要什么", text=None):
    default_lines = {
        "root": "README.md:1-1",
        "alpha": "alpha/main.py:1-1",
        "beta": "beta/main.py:1-1",
    }
    if text is None:
        text = {
            "要什么": f"提供 {feature_id} 功能。",
            "为什么": "让功能容易使用。",
            RULE_SOURCE: RULE_TEXT,
        }.get(source, "")
    return {
        "lines": default_lines[feature_id] if lines is None else lines,
        "requirement": {"source": source, "text": text},
        "achieved": "实现了这项要求。",
        "cost": "没有更小的做法，复杂度和代价配得上收益。",
    }


def review_feature(feature_id, role):
    common = {"feature": feature_id, "todo_items": [], "findings": []}
    if role == "主审":
        return {**common, "what_true": "实现与说明相符。",
                "why_holds": "理由仍成立。", "structure": "现有结构合适。"}
    default_features = {"root", "alpha", "beta"}
    keep = [razor_keep_item(feature_id)] if feature_id in default_features else []
    return {**common, "necessity": "功能必要。", "keep": keep}


def report(role, repository, feature_ids=("root", "alpha", "beta")):
    return {"role": role, "repo": str(repository),
            "features": [review_feature(feature_id, role) for feature_id in feature_ids]}


def finding(kind="重构", changes_behavior=True, decision="问你", basis=None):
    return {
        "kind": kind,
        "target": "alpha/main.py:1",
        "problem": "实现重复。",
        "proposal": "合并重复逻辑。",
        "changes_behavior": changes_behavior,
        "decision": decision,
        "basis": {"source": RULE_SOURCE, "text": RULE_TEXT} if basis is None else basis,
        "evidence": "两个位置执行相同逻辑。",
        "benefit": "减少重复代码。",
        "cost": "需要调整调用。",
    }


def todo_item(lines):
    return {"lines": lines, "needed": "继续处理。", "smaller": "可以保持简短。",
            "why_holds": "理由仍成立。"}


class CheckReportsTest(unittest.TestCase):
    def setUp(self):
        self.repository = make_repo(self, {
            "README.md": "Demo.\n",
            "alpha/main.py": "value = 1\n",
            "beta/main.py": "value = 2\n",
        }, feature("root", "", subrepositories=[], external_dependencies=[], children=[
            feature("alpha", "alpha"),
            feature("beta", "beta"),
        ]))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.main_path = self.directory / "main.json"
        self.razor_path = self.directory / "razor.json"
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))

    def write_reports(self, main, razor):
        self.main_path.write_text(json.dumps(main, ensure_ascii=False), encoding="utf-8")
        self.razor_path.write_text(json.dumps(razor, ensure_ascii=False), encoding="utf-8")

    def add_tracked_file(self, relative_path, content):
        path = self.repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", relative_path], check=True)

    def write_todo_reports(self, items_by_feature, findings_by_feature=None):
        findings_by_feature = findings_by_feature or {}
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        for document in (main, razor):
            for review in document["features"]:
                feature_id = review["feature"]
                if feature_id in items_by_feature:
                    review["todo_items"] = items_by_feature[feature_id]
                if feature_id in findings_by_feature:
                    review["findings"] = findings_by_feature[feature_id]
        self.write_reports(main, razor)

    def run_check(self, scope=("全部",), reports=None, review="全部"):
        paths = reports or (str(self.main_path), str(self.razor_path))
        arguments = ["python3", "-B", str(COMMAND), "--repo", str(self.repository),
                     "--scope", *scope]
        if review is not None:
            arguments.extend(("--review", review))
        arguments.extend(("--reports", *paths))
        return subprocess.run(
            arguments,
            capture_output=True, text=True, check=False,
        )

    def record(self, scope=("全部",)):
        return subprocess.run(
            ["python3", "-B", str(ROOT / "xy-cleanup" / "approvals.py"),
             "record", "--repo", str(self.repository), "--scope", *scope],
            capture_output=True, text=True, check=False,
        )

    def test_accepts_complete_reports_for_each_role(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_razor_requires_necessity(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        del alpha["necessity"]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("缺少字段 necessity", result.stdout)

    def test_razor_necessity_must_be_nonempty(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["necessity"] = "  "
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("features[1].necessity 必须是非空字符串", result.stdout)

    def test_main_role_rejects_necessity(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        main["features"][0]["necessity"] = "功能必要。"
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("有多余字段 necessity", result.stdout)

    def test_razor_keep_must_cover_each_nonempty_line_and_may_skip_blank_lines(self):
        self.add_tracked_file("alpha/guide.md", "开头。\n\n结尾。\n")
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = [
            razor_keep_item("alpha"),
            razor_keep_item("alpha", "alpha/guide.md:1-1"),
            razor_keep_item("alpha", "alpha/guide.md:3-3"),
        ]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        alpha["keep"] = [
            razor_keep_item("alpha"),
            razor_keep_item("alpha", "alpha/guide.md:1-1"),
        ]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/guide.md:3", result.stdout)

    def test_finding_range_can_cover_lines_but_delete_range_cannot_overlap_keep(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = []
        deletion = finding("删除", False, "直接做")
        deletion["target"] = "alpha/main.py:1-1"
        alpha["findings"] = [deletion]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        alpha["keep"] = [razor_keep_item("alpha")]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/main.py:1-1", result.stdout)
        self.assertIn("重叠", result.stdout)

    def test_feature_id_delete_covers_all_feature_files_when_keep_is_empty(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = []
        deletion = finding("删除", False, "直接做")
        deletion["target"] = "alpha"
        alpha["findings"] = [deletion]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_feature_id_delete_overlaps_any_keep(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        deletion = finding("删除", False, "直接做")
        deletion["target"] = "alpha"
        alpha["findings"] = [deletion]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("删除目标和 keep 行范围重叠", result.stdout)

    def test_other_feature_id_does_not_cover_this_features_files(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = []
        deletion = finding("删除", False, "直接做")
        deletion["target"] = "beta"
        alpha["findings"] = [deletion]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("未覆盖功能文件", result.stdout)
        self.assertIn("alpha/main.py:1", result.stdout)

    def test_main_report_does_not_classify_keep_files(self):
        data = check_reports_module.build_map(self.repository)
        tracked_files = check_reports_module.tracked_paths(self.repository)
        with patch.object(
            check_reports_module,
            "file_category",
            side_effect=AssertionError("主审不需要分类 keep 文件"),
        ):
            check_reports_module.validate_reports(
                data,
                self.repository,
                set(data["features"]),
                (self.main_path,),
                tracked_files,
            )

    def test_one_python_keep_cannot_cover_two_definitions(self):
        self.add_tracked_file(
            "alpha/combined.py",
            "def first():\n    return 1\ndef second():\n    return 2\n",
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"].append(razor_keep_item("alpha", "alpha/combined.py:1-4"))
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/combined.py:1-4", result.stdout)
        self.assertIn("一个实体", result.stdout)

    def test_class_methods_need_separate_keeps_and_nested_functions_are_not_entities(self):
        self.add_tracked_file(
            "alpha/service.py",
            "class Service:\n    def first(self):\n        return 1\n"
            "    def second(self):\n        return 2\n",
        )
        self.add_tracked_file(
            "alpha/wrappers.py",
            "def outer():\n    def first():\n        return 1\n"
            "    def second():\n        return 2\n    return first(), second()\n",
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"].extend([
            razor_keep_item("alpha", "alpha/service.py:1-1"),
            razor_keep_item("alpha", "alpha/service.py:2-3"),
            razor_keep_item("alpha", "alpha/service.py:4-5"),
            razor_keep_item("alpha", "alpha/wrappers.py:1-6"),
        ])
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_markdown_keep_cannot_cross_two_headings_but_code_fence_hash_is_not_a_heading(self):
        self.add_tracked_file("alpha/two-headings.md", "# First\nText.\n## Second\n")
        self.add_tracked_file(
            "alpha/code-sample.md", "# Guide\n```python\n# inside\n```\n"
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"].extend([
            razor_keep_item("alpha", "alpha/two-headings.md:1-3"),
            razor_keep_item("alpha", "alpha/code-sample.md:1-4"),
        ])
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/two-headings.md:1-3", result.stdout)
        self.assertIn("一个实体", result.stdout)

        alpha["keep"].remove(next(
            item for item in alpha["keep"]
            if item["lines"] == "alpha/two-headings.md:1-3"
        ))
        alpha["keep"].extend([
            razor_keep_item("alpha", "alpha/two-headings.md:1-1"),
            razor_keep_item("alpha", "alpha/two-headings.md:2-3"),
        ])
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_keep_cannot_cross_indented_atx_headings(self):
        self.add_tracked_file(
            "alpha/indented-headings.md",
            " # One\n  # Two\n   # Three\n",
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"].append(razor_keep_item("alpha", "alpha/indented-headings.md:1-3"))
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/indented-headings.md:1-3", result.stdout)
        self.assertIn("一个实体", result.stdout)

    def test_keep_cannot_cross_setext_equal_and_hyphen_headings(self):
        self.add_tracked_file(
            "alpha/setext-headings.md",
            "First\n===\nMiddle\nSecond\n---\n",
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"].append(razor_keep_item("alpha", "alpha/setext-headings.md:1-5"))
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/setext-headings.md:1-5", result.stdout)
        self.assertIn("一个实体", result.stdout)

    def test_yaml_front_matter_is_not_counted_as_markdown_headings(self):
        self.add_tracked_file(
            "alpha/front-matter.md",
            "---\n# YAML One\n## YAML Two\n---\n    # Four spaces\n",
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"].append(razor_keep_item("alpha", "alpha/front-matter.md:1-5"))
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_code_blocks_list_items_and_table_separators_are_not_headings(self):
        self.add_tracked_file(
            "alpha/markdown-non-headings.md",
            "    # Four spaces\n````md\n# Code One\n## Code Two\n````\n"
            "- list item\n---\n| left | right |\n| --- | --- |\n",
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"].append(
            razor_keep_item("alpha", "alpha/markdown-non-headings.md:1-9")
        )
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_list_marker_is_not_a_setext_underline(self):
        self.add_tracked_file("alpha/list-marker.md", "First\n-\nSecond\n---\n")
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"].append(razor_keep_item("alpha", "alpha/list-marker.md:1-4"))
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_python_parse_error_is_reported_when_checking_one_entity(self):
        self.add_tracked_file("alpha/broken.py", "def broken(:\n    return 1\n")
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"].append(razor_keep_item("alpha", "alpha/broken.py:1-2"))
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/broken.py", result.stdout)
        self.assertIn("无法检查", result.stdout)
        self.assertIn("一个实体", result.stdout)

    def test_keep_requirement_accepts_feature_text_and_supported_rule_quotes(self):
        for source, text in (
            ("要什么", "提供 alpha 功能。"),
            ("为什么", "让功能容易使用。"),
            (RULE_SOURCE, RULE_TEXT),
        ):
            with self.subTest(source=source):
                main = report("主审", self.repository)
                razor = report("剃刀", self.repository)
                alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
                alpha["keep"] = [razor_keep_item("alpha", source=source, text=text)]
                self.write_reports(main, razor)
                result = self.run_check()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_keep_requirement_must_be_a_continuous_quote(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = [razor_keep_item("alpha", source="要什么", text="功能存在。")]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("requirement.text", result.stdout)

    def test_keep_requirement_accepts_a_continuous_substring(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = [razor_keep_item(
            "alpha", source="要什么", text="alpha 功能",
        )]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_keep_requirement_rejects_text_joined_from_noncontiguous_parts(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = [razor_keep_item(
            "alpha", source="要什么", text="提供 功能",
        )]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("requirement.text", result.stdout)

    def test_keep_achieved_and_cost_must_be_nonempty(self):
        for key in ("achieved", "cost"):
            with self.subTest(key=key):
                main = report("主审", self.repository)
                razor = report("剃刀", self.repository)
                alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
                alpha["keep"][0][key] = "  "
                self.write_reports(main, razor)
                result = self.run_check()
                self.assertEqual(result.returncode, 1)
                self.assertIn(key, result.stdout)

    def test_draft_reason_requires_empty_keep_and_skips_keep_coverage(self):
        feature_path = self.repository / "repo-map" / "features.json"
        features = json.loads(feature_path.read_text(encoding="utf-8"))
        alpha_feature = next(item for item in features["children"] if item["id"] == "alpha")
        alpha_feature["why"] = {"text": "让功能容易使用。", "source": "起草",
                                 "quote": "", "from": ""}
        feature_path.write_text(json.dumps(features, ensure_ascii=False), encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "repo-map/features.json"], check=True)

        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("why.source 为 起草", result.stdout)

        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = []
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_todo_file_is_covered_by_todo_items_not_razor_keep(self):
        self.add_tracked_file("alpha/TODO.md", "待办事项。\n")
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        for document in (main, razor):
            alpha = next(item for item in document["features"] if item["feature"] == "alpha")
            alpha["todo_items"] = [todo_item("alpha/TODO.md:1-1")]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_todo_items_are_required_arrays(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        for document in (main, razor):
            for review in document["features"]:
                del review["todo_items"]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("todo_items", result.stdout)

        main = report("主审", self.repository)
        main["features"][0]["todo_items"] = None
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("todo_items", result.stdout)

    def test_non_string_feature_id_is_rejected_without_crashing(self):
        main = report("主审", self.repository, ("alpha",))
        main["features"][0]["feature"] = []
        self.write_reports(main, report("剃刀", self.repository, ("alpha",)))
        result = self.run_check(scope=("alpha",))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr, result.stderr)
        self.assertIn("不在功能清单中", result.stdout)

    def test_rejects_todo_items_without_a_direct_todo_file(self):
        self.write_todo_reports({"alpha": [todo_item("alpha/TODO.md:1-1")]})
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/TODO.md", result.stdout)

    def test_todo_items_cover_each_nonempty_line_without_covering_blank_lines(self):
        self.add_tracked_file(
            "alpha/TODO.md", "开头约定。\n第一条待办。\n\n第二条待办。\n"
        )
        self.write_todo_reports({
            "alpha": [todo_item("alpha/TODO.md:1-2"), todo_item("alpha/TODO.md:4-4")]
        })
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.write_todo_reports({
            "alpha": [todo_item("alpha/TODO.md:1-1"), todo_item("alpha/TODO.md:4-4")]
        })
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/TODO.md", result.stdout)
        self.assertIn("alpha/TODO.md:2", result.stdout)

    def test_rejects_overlapping_todo_item_lines(self):
        self.add_tracked_file("alpha/TODO.md", "约定。\n待办一。\n待办二。\n")
        self.write_todo_reports({
            "alpha": [todo_item("alpha/TODO.md:1-2"), todo_item("alpha/TODO.md:2-3")]
        })
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/TODO.md", result.stdout)

    def test_todo_item_lines_must_name_a_valid_direct_todo_range(self):
        self.add_tracked_file("alpha/TODO.md", "约定。\n待办。\n")
        self.add_tracked_file("beta/TODO.md", "Beta 约定。\n")
        invalid_lines = (
            "alpha/TODO.md",
            "alpha/TODO.md:2",
            "alpha/TODO.md:2-1",
            "alpha/TODO.md:0-1",
            "alpha/TODO.md:1-3",
            "alpha/TODO.md:one-two",
            "alpha/main.py:1-1",
            "beta/TODO.md:1-1",
        )
        for lines in invalid_lines:
            with self.subTest(lines=lines):
                self.write_todo_reports({
                    "alpha": [todo_item(lines)],
                    "beta": [todo_item("beta/TODO.md:1-1")],
                })
                result = self.run_check()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("alpha", result.stdout)
                self.assertIn(lines, result.stdout)

    def test_todo_item_lines_must_be_nonempty_strings(self):
        for lines in ("", "  ", 5):
            with self.subTest(lines=lines):
                self.write_todo_reports({"alpha": [todo_item(lines)]})
                result = self.run_check()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("lines", result.stdout)

    def test_todo_item_requires_exact_keys_and_nonempty_answers(self):
        self.add_tracked_file("alpha/TODO.md", "约定。\n")
        invalid_items = [
            {"lines": "alpha/TODO.md:1-1", "needed": "要做。", "smaller": "简短。"},
            {**todo_item("alpha/TODO.md:1-1"), "extra": "不允许。"},
            {**todo_item("alpha/TODO.md:1-1"), "why_holds": "  "},
        ]
        for item in invalid_items:
            with self.subTest(item=item):
                self.write_todo_reports({"alpha": [item]})
                result = self.run_check()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("todo_items", result.stdout)

    def test_root_todo_is_owned_by_root_and_not_alpha(self):
        self.add_tracked_file("TODO.md", "仓库约定。\n")
        self.add_tracked_file("alpha/TODO.md", "Alpha 约定。\n")
        self.write_todo_reports({
            "root": [todo_item("TODO.md:1-1")],
            "alpha": [todo_item("alpha/TODO.md:1-1")],
        })
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_todo_delete_must_be_asked_while_other_nonbehavioral_delete_is_direct(self):
        self.add_tracked_file("alpha/TODO.md", "约定。\n过时事项。\n")
        asked_todo_delete = finding("删除", False, "问你")
        asked_todo_delete["target"] = "alpha/TODO.md:2-2"
        self.write_todo_reports(
            {"alpha": [todo_item("alpha/TODO.md:1-2")]},
            {"root": [asked_todo_delete]},
        )
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        direct_todo_delete = finding("删除", False, "直接做")
        direct_todo_delete["target"] = "alpha/TODO.md:2-2"
        self.write_todo_reports(
            {"alpha": [todo_item("alpha/TODO.md:1-2")]},
            {"root": [direct_todo_delete]},
        )
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/TODO.md:2", result.stdout)

        direct_file_delete = finding("删除", False, "直接做")
        direct_file_delete["target"] = "alpha/main.py:1"
        self.write_todo_reports(
            {"alpha": [todo_item("alpha/TODO.md:1-2")]},
            {"root": [direct_file_delete]},
        )
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_requires_exact_top_level_and_feature_fields(self):
        main = report("主审", self.repository)
        main["legacy"] = "unused"
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("legacy", result.stdout)

        cases = [
            ("主审", "keep", []),
            ("剃刀", "structure", "结构回答"),
            ("主审", "needed", "旧答案"),
            ("剃刀", "needed", "旧答案"),
        ]
        for role, key, value in cases:
            with self.subTest(role=role, key=key):
                main = report("主审", self.repository)
                razor = report("剃刀", self.repository)
                document = main if role == "主审" else razor
                document["features"][0][key] = value
                self.write_reports(main, razor)
                result = self.run_check()
                self.assertEqual(result.returncode, 1)
                self.assertIn(key, result.stdout)

    def test_what_true_is_required_and_nonempty(self):
        main = report("主审", self.repository)
        main["features"][0]["what_true"] = "实现与说明相符。"
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        main["features"][0]["what_true"] = "  "
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("what_true", result.stdout)

        del main["features"][0]["what_true"]
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("what_true", result.stdout)

    def test_review_mode_is_required_and_restricted(self):
        result = self.run_check(review=None)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

        for review in ("变过的", "全部"):
            with self.subTest(review=review):
                result = self.run_check(review=review)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        result = self.run_check(review="不认识")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_changed_review_uses_the_approval_plan_for_report_coverage(self):
        recorded = self.record()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

        self.write_reports(report("主审", self.repository, ()),
                           report("剃刀", self.repository, ()))
        result = self.run_check(review="变过的")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        (self.repository / "alpha" / "main.py").write_text("value = 100\n", encoding="utf-8")
        self.write_reports(report("主审", self.repository, ("alpha",)),
                           report("剃刀", self.repository, ("alpha",)))
        result = self.run_check(review="变过的")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_empty_answers_and_extra_finding_fields(self):
        main = report("主审", self.repository)
        main["features"][0]["why_holds"] = "  "
        main["features"][0]["findings"] = [finding()]
        main["features"][0]["findings"][0]["extra"] = "not allowed"
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("why_holds", result.stdout)
        self.assertIn("extra", result.stdout)

    def test_requires_every_scoped_feature_once_for_each_role(self):
        cases = [
            (("root", "beta"), ("root", "alpha", "beta"), "alpha"),
            (("root", "alpha", "alpha", "beta"), ("root", "alpha", "beta"), "重复"),
            (("root", "alpha", "beta"), ("root", "alpha"), "beta"),
        ]
        for main_ids, razor_ids, expected in cases:
            with self.subTest(main=main_ids, razor=razor_ids):
                self.write_reports(report("主审", self.repository, main_ids),
                                   report("剃刀", self.repository, razor_ids))
                result = self.run_check()
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stdout)

    def test_a_parent_scope_expands_to_its_finest_features(self):
        root_path = self.repository / "repo-map" / "features.json"
        root = json.loads(root_path.read_text(encoding="utf-8"))
        root["children"].append(feature("group", None, children=[
            feature("gamma", None, entries=[]), feature("delta", None, entries=[]),
        ]))
        root_path.write_text(json.dumps(root, ensure_ascii=False), encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "-A"], check=True)
        main = report("主审", self.repository, ("group", "gamma", "delta"))
        razor = report("剃刀", self.repository, ("group", "gamma", "delta"))
        self.write_reports(main, razor)
        result = self.run_check(("group",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_role_may_split_its_feature_coverage_across_reports(self):
        second_main = self.directory / "main-second.json"
        second_main.write_text(json.dumps(report("主审", self.repository, ("beta",)),
                                          ensure_ascii=False), encoding="utf-8")
        self.main_path.write_text(json.dumps(report("主审", self.repository, ("root", "alpha")),
                                             ensure_ascii=False), encoding="utf-8")
        result = self.run_check(reports=(str(self.main_path), str(second_main), str(self.razor_path)))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_a_non_boolean_behavior_value_and_an_out_of_scope_feature(self):
        main = report("主审", self.repository)
        bad = finding()
        bad["changes_behavior"] = 1
        main["features"][0]["findings"] = [bad]
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("布尔值", result.stdout)

        main["features"].insert(0, review_feature("root", "主审"))
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check(scope=("alpha",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("报告功能不在本次审查名单里：root", result.stdout)

    def test_rejects_a_finding_with_missing_fields_or_invalid_values(self):
        main = report("主审", self.repository)
        bad = finding()
        del bad["evidence"]
        main["features"][0]["findings"] = [bad]
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("evidence", result.stdout)

        main["features"][0]["findings"] = [finding(kind="不支持")]
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("kind", result.stdout)

    def test_direct_decision_is_only_for_nonbehavioral_delete_or_merge(self):
        valid = [
            finding("删除", False, "直接做"),
            finding("合并", False, "直接做"),
            finding("删除", True, "问你"),
            finding("重构", False, "问你"),
            finding("改清单", True, "问你"),
        ]
        for item in valid:
            with self.subTest(kind=item["kind"], changes=item["changes_behavior"], decision=item["decision"]):
                main = report("主审", self.repository)
                main["features"][0]["findings"] = [item]
                self.write_reports(main, report("剃刀", self.repository))
                result = self.run_check()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        invalid = [
            finding("删除", False, "问你"),
            finding("删除", True, "直接做"),
            finding("重构", False, "直接做"),
            finding("改清单", False, "直接做"),
        ]
        for item in invalid:
            with self.subTest(kind=item["kind"], changes=item["changes_behavior"], decision=item["decision"]):
                main = report("主审", self.repository)
                main["features"][0]["findings"] = [item]
                self.write_reports(main, report("剃刀", self.repository))
                self.assertEqual(self.run_check().returncode, 1)

    def test_basis_must_match_a_local_rule_quote(self):
        main = report("主审", self.repository)
        main["features"][0]["findings"] = [finding(basis={"source": RULE_SOURCE, "text": "拼接的引文"})]
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("basis", result.stdout)

    def test_rejects_duplicate_json_keys_in_reports(self):
        self.main_path.write_text(
            '{"role":"主审","role":"剃刀"}', encoding="utf-8"
        )
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            f"无法读取报告 {self.main_path}：重复键：role", result.stdout
        )

    def test_finding_target_must_be_a_tracked_feature_or_valid_file_range(self):
        main = report("主审", self.repository)
        main["features"][0]["findings"] = [finding()]
        self.write_reports(main, report("剃刀", self.repository))
        self.assertEqual(self.run_check().returncode, 0)

        main["features"][0]["findings"][0]["target"] = "alpha/main.py:1-1"
        self.write_reports(main, report("剃刀", self.repository))
        self.assertEqual(self.run_check().returncode, 0)

        invalid_targets = (
            "alpha/main.py:2",
            "alpha/main.py:1-2",
            "missing.py:1",
            "alpha/main.py:0",
            "alpha/main.py:2-1",
            "alpha/main.py:line",
            "alpha/main.py:" + "9" * 5000,
        )
        for target in invalid_targets:
            with self.subTest(target=target):
                main = report("主审", self.repository)
                bad_finding = finding()
                bad_finding["target"] = target
                main["features"][0]["findings"] = [bad_finding]
                self.write_reports(main, report("剃刀", self.repository))
                result = self.run_check()
                self.assertEqual(result.returncode, 1)
                self.assertIn(
                    "features[0].findings[0].target 必须是功能 id、文件:行号或 文件:起始行-结束行："
                    + target,
                    result.stdout,
                )

        main = report("主审", self.repository)
        feature_target = finding()
        feature_target["target"] = "alpha"
        main["features"][0]["findings"] = [feature_target]
        self.write_reports(main, report("剃刀", self.repository))
        self.assertEqual(self.run_check().returncode, 0)

    def test_finding_target_accepts_only_existing_lines_of_a_tracked_symlink(self):
        link_path = self.repository / "alpha" / "source-link"
        link_path.symlink_to("main.py")
        subprocess.run(
            ["git", "-C", str(self.repository), "add", "alpha/source-link"],
            check=True,
        )

        main = report("主审", self.repository)
        link_finding = finding()
        link_finding["target"] = "alpha/source-link:1"
        main["features"][0]["findings"] = [link_finding]
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        link_finding["target"] = "alpha/source-link:2"
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/source-link:2", result.stdout)

    def test_requires_both_review_roles_and_matching_repository(self):
        result = self.run_check(reports=(str(self.main_path),))
        self.assertEqual(result.returncode, 1)
        self.assertIn("剃刀", result.stdout)
        razor = report("剃刀", self.repository)
        razor["repo"] = "/different/repo"
        self.write_reports(report("主审", self.repository), razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("repo", result.stdout)

    def test_bad_scope_arguments_exit_with_two(self):
        result = self.run_check(("全部", "alpha"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("--scope 使用 全部 时不能再列功能 id", result.stderr)
        result = self.run_check(("alpha", "alpha"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("--scope 不能重复列出功能 id", result.stderr)
        result = self.run_check(("missing",))
        self.assertEqual(result.returncode, 2)
        self.assertIn("--scope 中有未知功能 id：missing", result.stderr)


if __name__ == "__main__":
    unittest.main()
