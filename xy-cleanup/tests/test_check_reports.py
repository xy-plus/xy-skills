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

from map_fixture import feature, make_cleanup_repo, make_repo  # noqa: E402
import check_reports as check_reports_module  # noqa: E402


COMMAND = ROOT / "xy-cleanup" / "check_reports.py"
RULE_SOURCE = "xy-iron-law 第 3 条"
RULE_TEXT = "如无必要，勿增实体（奥卡姆剃刀）。"
DUPLICATE_BLOCK_LINES = [
    "This shared paragraph explains the first concrete behavior clearly.",
    "This shared paragraph explains the second concrete behavior clearly.",
    "This shared paragraph explains the third concrete behavior clearly.",
    "This shared paragraph explains the fourth concrete behavior clearly.",
    "This shared paragraph explains the fifth concrete behavior clearly.",
    "This shared paragraph explains the sixth concrete behavior clearly.",
]


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
    line_spec = default_lines[feature_id] if lines is None else lines
    return {
        "lines": line_spec,
        "requirement": {"source": source, "text": text},
        "achieved": f"{line_spec} 实现了这项要求。",
        "cost": f"{line_spec} 没有更小的做法，复杂度和代价配得上收益。",
    }


def review_feature(feature_id, role, own_lines=None):
    common = {"feature": feature_id, "todo_items": [], "findings": []}
    if role == "主审":
        return {**common, "what_true": "实现与说明相符。",
                "why_holds": "理由仍成立。", "structure": "现有结构合适。"}
    default_features = {"root", "alpha", "beta"}
    keep = [razor_keep_item(feature_id)] if feature_id in default_features else []
    return {
        **common,
        "necessity": "功能必要。",
        "smallest": {
            "design": "按当前做法实现。",
            "lines": own_lines or {"code": 0, "docs": 0, "comments": 0, "tests": 0},
        },
        "keep": keep,
    }


def report(role, repository, feature_ids=("root", "alpha", "beta")):
    own_lines = {}
    if role == "剃刀":
        feature_data = check_reports_module.build_map(repository)["features"]
        own_lines = {
            feature_id: feature_data[feature_id]["lines"]["own"]
            for feature_id in feature_ids
        }
    return {"role": role, "repo": str(repository),
            "features": [
                review_feature(feature_id, role, own_lines[feature_id])
                if role == "剃刀" else review_feature(feature_id, role)
                for feature_id in feature_ids
            ]}


def recheck_verdict(lines, verdict="站得住", attempt="试着移除实体并核对结果。",
                    evidence=None):
    if evidence is None:
        own_path = lines.rpartition(":")[0]
        cited_path = "alpha/main.py" if own_path == "README.md" else "README.md"
        evidence = f"对照了 `{cited_path}:1` 的内容。"
    return {
        "lines": lines,
        "attempt": attempt,
        "evidence": evidence,
        "verdict": verdict,
    }


def recheck_feature(feature_id, findings, verdicts=None):
    default_lines = {
        "root": "README.md:1-1",
        "alpha": "alpha/main.py:1-1",
        "beta": "beta/main.py:1-1",
    }
    if verdicts is None:
        verdicts = (
            [recheck_verdict(default_lines[feature_id])]
            if feature_id in default_lines else []
        )
    return {"feature": feature_id, "verdicts": verdicts, "findings": findings}


def recheck_report(repository, feature_ids, keep_lines=None):
    if keep_lines is not None and len(feature_ids) == 1:
        features = [
            recheck_feature(
                feature_ids[0], [],
                verdicts=[recheck_verdict(lines) for lines in keep_lines],
            )
        ]
    else:
        features = [recheck_feature(feature_id, []) for feature_id in feature_ids]
    return {
        "role": "剃刀复审",
        "repo": str(repository),
        "features": features,
    }


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
        self.repository = make_cleanup_repo(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.main_path = self.directory / "main.json"
        self.razor_path = self.directory / "razor.json"
        self.recheck_path = self.directory / "recheck.json"
        self.recheck_paths = []
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))

    def write_reports(self, main, razor):
        self.main_path.write_text(json.dumps(main, ensure_ascii=False), encoding="utf-8")
        self.razor_path.write_text(json.dumps(razor, ensure_ascii=False), encoding="utf-8")
        data = check_reports_module.build_map(self.repository)
        required = []
        for item in razor["features"]:
            feature_id = item["feature"]
            feature_data = data["features"].get(feature_id)
            if feature_data is None or feature_data["why"]["source"] == "起草":
                continue
            direct_files = {
                path
                for group_paths in feature_data["files"].values()
                for path in group_paths
            }
            keep_files = {
                path for path in direct_files
                if Path(path).name != "TODO.md"
                and check_reports_module.file_category(self.repository, path)
                in {"code", "tests", "docs"}
            }
            has_reduction = any(
                isinstance(item_finding, dict)
                and item_finding.get("kind") in {"删除", "合并"}
                and (
                    item_finding.get("target") == feature_id
                    or (
                        (target_range := check_reports_module._parse_line_range(
                            item_finding.get("target"), allow_single_line=True,
                        )) is not None
                        and target_range[0] in keep_files
                    )
                )
                for item_finding in item["findings"]
            )
            if keep_files and not has_reduction:
                required.append(recheck_report(
                    self.repository,
                    [feature_id],
                    keep_lines=[
                        item_keep["lines"]
                        for item_keep in item["keep"]
                        if isinstance(item_keep, dict)
                        and isinstance(item_keep.get("lines"), str)
                    ],
                ))
        if required:
            self.write_recheck_reports(required)
        else:
            self.clear_recheck_reports()

    def write_recheck_report(self, recheck):
        self.write_recheck_reports([recheck])

    def write_recheck_reports(self, reports):
        self.clear_recheck_reports()
        for index, recheck in enumerate(reports):
            path = self.recheck_path if index == 0 else self.directory / f"recheck-{index}.json"
            path.write_text(json.dumps(recheck, ensure_ascii=False), encoding="utf-8")
            self.recheck_paths.append(path)

    def clear_recheck_reports(self):
        for path in self.recheck_paths:
            path.unlink(missing_ok=True)
        self.recheck_path.unlink(missing_ok=True)
        self.recheck_paths = []

    def read_recheck_reports(self):
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in self.recheck_paths
        ]

    def read_recheck_for_feature(self, feature_id):
        return next(
            recheck for recheck in self.read_recheck_reports()
            if any(item.get("feature") == feature_id for item in recheck["features"])
        )

    def reports_for_check(self):
        return (
            str(self.main_path),
            str(self.razor_path),
            *(str(path) for path in self.recheck_paths),
        )

    def recheck_scenario(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        for feature_id, kind in (("root", "删除"), ("beta", "合并")):
            item = next(entry for entry in razor["features"] if entry["feature"] == feature_id)
            item["keep"] = []
            removal = finding(kind, False, "直接做")
            removal["target"] = feature_id
            item["findings"] = [removal]
        alpha = next(entry for entry in razor["features"] if entry["feature"] == "alpha")
        alpha["findings"] = [finding("重构", True, "问你")]
        return main, razor

    def add_empty_feature(self):
        map_path = self.repository / "repo-map" / "features.json"
        feature_map = json.loads(map_path.read_text(encoding="utf-8"))
        feature_map["children"].append(feature("empty", None))
        self.add_tracked_file(
            "repo-map/features.json", json.dumps(feature_map, ensure_ascii=False)
        )

    def add_tracked_file(self, relative_path, content):
        path = self.repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", relative_path], check=True)

    def check_duplicate_documents(self, first_lines, second_lines,
                                  first_cost=None, second_cost=None):
        self.add_tracked_file("alpha/first.md", "\n".join(first_lines) + "\n")
        self.add_tracked_file("alpha/second.md", "\n".join(second_lines) + "\n")
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        first_keep = razor_keep_item("alpha", f"alpha/first.md:1-{len(first_lines)}")
        second_keep = razor_keep_item("alpha", f"alpha/second.md:1-{len(second_lines)}")
        if first_cost is not None:
            first_keep["cost"] = first_cost
        if second_cost is not None:
            second_keep["cost"] = second_cost
        alpha["keep"] = [razor_keep_item("alpha"), first_keep, second_keep]
        self.write_reports(main, razor)
        return self.run_check()

    def write_split_razor_reports(self, razor_documents):
        main = report("主审", self.repository)
        self.main_path.write_text(json.dumps(main, ensure_ascii=False), encoding="utf-8")
        razor_paths = []
        rechecks = []
        for index, document in enumerate(razor_documents):
            path = self.razor_path if index == 0 else self.directory / f"razor-{index}.json"
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            razor_paths.append(path)
            for item in document["features"]:
                rechecks.append(recheck_report(
                    self.repository,
                    [item["feature"]],
                    keep_lines=[keep["lines"] for keep in item["keep"]],
                ))
        self.write_recheck_reports(rechecks)
        return [str(self.main_path), *(str(path) for path in razor_paths)] + [
            str(path) for path in self.recheck_paths
        ]

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
        paths = reports
        if paths is None:
            paths = (str(self.main_path), str(self.razor_path))
            paths += tuple(str(path) for path in self.recheck_paths if path.exists())
        arguments = ["python3", "-B", str(COMMAND), "--repo", str(self.repository),
                     "--scope", *scope]
        if review is not None:
            arguments.extend(("--review", review))
        arguments.extend(("--reports", *paths))
        return subprocess.run(
            arguments,
            capture_output=True, text=True, check=False,
        )

    def test_accepts_complete_reports_for_each_role(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_skipped_document_and_todo_need_no_keep_or_todo_review(self):
        self.add_tracked_file("alpha/guide.md", "A long experimental guide.\n")
        self.add_tracked_file("alpha/TODO.md", "An experimental reminder.\n")
        self.add_tracked_file(
            "repo-map/skip.json",
            json.dumps(["alpha/guide.md", "alpha/TODO.md"]),
        )

        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))

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

    def test_razor_requires_smallest(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        del alpha["smallest"]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("缺少字段 smallest", result.stdout)

    def test_razor_smallest_requires_exact_fields_and_nonempty_design(self):
        cases = [
            ({"lines": {"code": 1, "docs": 0, "comments": 0, "tests": 0}},
             "smallest缺少字段 design"),
            ({"design": "  ", "lines": {"code": 1, "docs": 0, "comments": 0, "tests": 0}},
             "smallest.design 必须是非空字符串"),
            ({"design": "当前做法。", "lines": {"docs": 0, "comments": 0, "tests": 0}},
             "smallest.lines缺少字段 code"),
            ({"design": "当前做法。", "lines": {"code": 1, "docs": 0, "comments": 0, "tests": 0, "extra": 0}},
             "smallest.lines有多余字段 extra"),
            ({"design": "当前做法。", "lines": {"code": -1, "docs": 0, "comments": 0, "tests": 0}},
             "smallest.lines.code 必须是非负整数"),
            ({"design": "当前做法。", "lines": {"code": True, "docs": 0, "comments": 0, "tests": 0}},
             "smallest.lines.code 必须是非负整数"),
            ({"design": "当前做法。", "lines": {"code": 1.5, "docs": 0, "comments": 0, "tests": 0}},
             "smallest.lines.code 必须是非负整数"),
            ({"design": "当前做法。", "lines": {"code": 1, "docs": 0, "comments": 0, "tests": 0}, "extra": "多余"},
             "smallest有多余字段 extra"),
        ]
        for smallest, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                main = report("主审", self.repository)
                razor = report("剃刀", self.repository)
                alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
                alpha["smallest"] = smallest
                self.write_reports(main, razor)
                result = self.run_check()
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected_error, result.stdout)

    def test_smaller_estimates_need_a_matching_feature_reduction(self):
        cases = (
            ("no finding", None, False),
            ("delete feature", ("删除", "alpha"), True),
            ("merge feature", ("合并", "alpha"), True),
            ("refactor feature", ("重构", "alpha"), True),
            ("line finding", ("删除", "alpha/main.py:1"), False),
            ("other feature", ("删除", "beta"), False),
            ("other kind", ("改名", "alpha"), False),
            ("equal estimate", None, True),
            ("higher estimate", None, True),
        )
        for name, candidate, accepted in cases:
            with self.subTest(case=name):
                main = report("主审", self.repository)
                razor = report("剃刀", self.repository)
                alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
                if name not in {"equal estimate", "higher estimate"}:
                    alpha["smallest"]["lines"]["code"] = 0
                else:
                    alpha["smallest"]["lines"]["code"] = 1 if name == "equal estimate" else 2
                if candidate is not None:
                    kind, target = candidate
                    proposal = finding(
                        kind, changes_behavior=False,
                        decision="直接做" if kind in {"删除", "合并"} else "问你",
                    )
                    proposal["target"] = target
                    alpha["findings"] = [proposal]
                    if name == "delete feature" or name == "line finding":
                        alpha["keep"] = []
                self.write_reports(main, razor)
                result = self.run_check()
                if accepted:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("smallest 估算少于当前行数", result.stdout)

    def test_nonstanding_verdict_requires_a_feature_deletion(self):
        cases = (("no finding", None, False),
                 ("refactor", "重构", False),
                 ("deletion", "删除", True))
        for name, kind, accepted in cases:
            with self.subTest(case=name):
                self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
                rechecks = self.read_recheck_reports()
                recheck = next(document for document in rechecks
                               if document["features"][0]["feature"] == "alpha")
                alpha = recheck["features"][0]
                alpha["verdicts"][0]["verdict"] = "站不住"
                if kind is not None:
                    proposal = finding(kind, False, "问你")
                    proposal["target"] = "alpha"
                    alpha["findings"] = [proposal]
                self.write_recheck_reports(rechecks)
                result = self.run_check()
                if accepted:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("alpha/main.py:1-1", result.stdout)

    def test_recheck_evidence_requires_another_valid_position(self):
        cases = (
            ("没有可核验位置", "检查了行为，没有可核验的位置。", False),
            ("未跟踪文件", "检查了 `missing.py:1`。", False),
            ("文件外行号", "查看 `README.md:99`。", False),
            ("只引用本项", "检查 `alpha/main.py:1` 的实体本身。", False),
            ("有效外部位置", "对照 `README.md:1-1`，该处说明了目标行为。", True),
        )
        for name, evidence, accepted in cases:
            with self.subTest(case=name):
                self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
                rechecks = self.read_recheck_reports()
                recheck = next(document for document in rechecks
                               if document["features"][0]["feature"] == "alpha")
                recheck["features"][0]["verdicts"][0]["evidence"] = evidence
                self.write_recheck_reports(rechecks)
                result = self.run_check()
                if accepted:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("evidence 必须引至少一处有效位置", result.stdout)
                    self.assertIn("alpha/main.py:1-1", result.stdout)

    def test_repeated_block_requires_cost_to_name_another_copy(self):
        result = self.check_duplicate_documents(
            DUPLICATE_BLOCK_LINES,
            [f"  {line}" for line in DUPLICATE_BLOCK_LINES],
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/second.md:1-6", result.stdout)
        self.assertIn("另一份", result.stdout)

    def test_duplicate_cost_location_must_be_valid_and_overlap_another_copy(self):
        first_lines = DUPLICATE_BLOCK_LINES + ["A unique final line follows the repeated block."]
        second_lines = [f"  {line}" for line in DUPLICATE_BLOCK_LINES] + [
            "A different unique final line follows the repeated block."
        ]
        invalid_costs = (
            ("alpha/second.md:7", "alpha/first.md:7"),
            ("alpha/second.md:99", "alpha/first.md:99"),
            ("missing.md:1", "missing.md:1"),
        )
        for first_cost, second_cost in invalid_costs:
            with self.subTest(first_cost=first_cost):
                result = self.check_duplicate_documents(
                    first_lines, second_lines,
                    f"另一份在 {first_cost}。",
                    f"另一份在 {second_cost}。",
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("alpha/second.md:1-6", result.stdout)

        result = self.check_duplicate_documents(
            first_lines,
            second_lines,
            "另一份在 alpha/second.md:1-6。",
            "另一份在 alpha/first.md:1-6。",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_duplicate_windows_ignore_blank_and_punctuation_lines_but_apply_both_cutoffs(self):
        first_lines = [
            DUPLICATE_BLOCK_LINES[0], "", DUPLICATE_BLOCK_LINES[1], "}",
            DUPLICATE_BLOCK_LINES[2], ")", DUPLICATE_BLOCK_LINES[3],
            "|---|---|", DUPLICATE_BLOCK_LINES[4], DUPLICATE_BLOCK_LINES[5],
        ]
        second_lines = [f"  {line}" if line else "   " for line in first_lines]
        result = self.check_duplicate_documents(first_lines, second_lines)
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/second.md:1-10", result.stdout)

        result = self.check_duplicate_documents(
            DUPLICATE_BLOCK_LINES[:5], DUPLICATE_BLOCK_LINES[:5],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        low_information_lines = ["x", "y", "z", "a", "b", "c"]
        result = self.check_duplicate_documents(
            low_information_lines, low_information_lines,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_repeated_windows_in_one_file_name_the_other_range(self):
        content_lines = (
            DUPLICATE_BLOCK_LINES
            + [""]
            + DUPLICATE_BLOCK_LINES
        )
        self.add_tracked_file("alpha/repeated.md", "\n".join(content_lines) + "\n")
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = [
            razor_keep_item("alpha"),
            razor_keep_item("alpha", "alpha/repeated.md:1-6"),
            razor_keep_item("alpha", "alpha/repeated.md:8-13"),
        ]
        self.write_reports(main, razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/repeated.md:8-13", result.stdout)
        self.assertIn("alpha/repeated.md:1-6", result.stdout)

        repeated = next(item for item in razor["features"] if item["feature"] == "alpha")
        repeated["keep"][1]["cost"] = "另一份在 alpha/repeated.md:8-13。"
        repeated["keep"][2]["cost"] = "另一份在 alpha/repeated.md:1-6。"
        periodic_lines = [
            "A separate periodic passage documents a distinct useful rule.",
            "Another periodic passage documents another distinct useful rule.",
            "Further periodic passage documents one more distinct useful rule.",
        ]
        periodic_lines = periodic_lines * 3
        self.add_tracked_file("alpha/periodic.md", "\n".join(periodic_lines) + "\n")
        repeated["keep"].append(razor_keep_item("alpha", "alpha/periodic.md:1-9"))
        repeated["smallest"]["lines"] = (
            check_reports_module.build_map(self.repository)["features"]["alpha"]["lines"]["own"]
        )
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_adjacent_duplicate_locations_are_merged_in_the_error(self):
        seven_shared_lines = DUPLICATE_BLOCK_LINES + [
            "This shared paragraph extends the same concrete behavior further.",
        ]
        result = self.check_duplicate_documents(
            seven_shared_lines,
            seven_shared_lines,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/second.md:1-7", result.stdout)

    def test_todo_file_is_not_a_duplicate_copy_for_keep(self):
        content = "\n".join(DUPLICATE_BLOCK_LINES) + "\n"
        self.add_tracked_file("alpha/guide.md", content)
        self.add_tracked_file("alpha/TODO.md", content)
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = [
            razor_keep_item("alpha"),
            razor_keep_item("alpha", "alpha/guide.md:1-6"),
        ]
        todo = [todo_item("alpha/TODO.md:1-6")]
        alpha["todo_items"] = todo
        main_alpha = next(item for item in main["features"] if item["feature"] == "alpha")
        main_alpha["todo_items"] = todo
        self.write_reports(main, razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_untracked_copy_is_not_scanned(self):
        result = self.check_duplicate_documents(
            DUPLICATE_BLOCK_LINES,
            DUPLICATE_BLOCK_LINES,
        )
        self.assertEqual(result.returncode, 1)
        subprocess.run(
            ["git", "-C", str(self.repository), "rm", "--cached", "--quiet", "alpha/second.md"],
            check=True,
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = [
            razor_keep_item("alpha"),
            razor_keep_item("alpha", "alpha/first.md:1-6"),
        ]
        self.write_reports(main, razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_feature_deletion_does_not_require_a_keep_duplicate_reference(self):
        self.add_tracked_file(
            "alpha/first.md", "\n".join(DUPLICATE_BLOCK_LINES) + "\n",
        )
        self.add_tracked_file(
            "alpha/second.md", "\n".join(DUPLICATE_BLOCK_LINES) + "\n",
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = []
        removal = finding("删除", changes_behavior=False, decision="直接做")
        removal["target"] = "alpha"
        alpha["findings"] = [removal]
        self.write_reports(main, razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_razor_recheck_findings_do_not_need_duplicate_keep_locations(self):
        self.check_duplicate_documents(
            DUPLICATE_BLOCK_LINES,
            [f"  {line}" for line in DUPLICATE_BLOCK_LINES],
            "另一份在 alpha/second.md:1-6。",
            "另一份在 alpha/first.md:1-6。",
        )
        keep_lines = [
            "alpha/main.py:1-1",
            "alpha/first.md:1-6",
            "alpha/second.md:1-6",
        ]
        recheck = recheck_report(self.repository, ["alpha"], keep_lines=keep_lines)
        review = recheck["features"][0]
        for verdict in review["verdicts"]:
            if verdict["lines"] == "alpha/first.md:1-6":
                verdict["verdict"] = "站不住"
        challenge = finding("删除", changes_behavior=False, decision="问你")
        challenge["target"] = "alpha/first.md:1-6"
        review["findings"] = [challenge]
        for path in self.recheck_paths:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing["features"][0]["feature"] == "alpha":
                path.write_text(json.dumps(recheck, ensure_ascii=False), encoding="utf-8")
                break

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_draft_feature_with_no_keep_skips_duplicate_location_rule(self):
        feature_path = self.repository / "repo-map" / "features.json"
        features = json.loads(feature_path.read_text(encoding="utf-8"))
        alpha_feature = features["children"][0]
        alpha_feature["why"] = {
            "text": "待用户确认的功能理由。",
            "source": "起草",
            "quote": "",
            "from": "",
        }
        self.add_tracked_file(
            "repo-map/features.json", json.dumps(features, ensure_ascii=False),
        )
        for relative in ("alpha/first.md", "alpha/second.md"):
            self.add_tracked_file(
                relative, "\n".join(DUPLICATE_BLOCK_LINES) + "\n",
            )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = []
        self.write_reports(main, razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_empty_razor_keep_does_not_scan_duplicate_windows(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        for review in razor["features"]:
            review["keep"] = []
        self.write_reports(main, razor)
        data = check_reports_module.build_map(self.repository)
        tracked_files = check_reports_module.tracked_paths(self.repository)

        with patch.object(check_reports_module, "find_duplicate_windows", return_value={}) as scan:
            check_reports_module.validate_reports(
                data,
                self.repository,
                set(data["features"]),
                self.reports_for_check(),
                tracked_files,
            )

        scan.assert_not_called()

    def test_razor_keep_reasons_cannot_repeat_across_features_or_reports(self):
        for field in ("achieved", "cost"):
            with self.subTest(field=field):
                documents = []
                for feature_id in ("alpha", "beta", "root"):
                    document = report("剃刀", self.repository, (feature_id,))
                    keep = document["features"][0]["keep"][0]
                    keep[field] = "  同一句理由  " if feature_id == "alpha" else "同一句理由"
                    documents.append(document)
                reports = self.write_split_razor_reports(documents)

                result = self.run_check(reports=reports)

                self.assertEqual(result.returncode, 1)
                self.assertIn(f"keep.{field} 逐字相同", result.stdout)
                self.assertIn("alpha/main.py:1-1", result.stdout)
                self.assertIn("beta/main.py:1-1", result.stdout)

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

    def test_missing_recheck_report_lists_each_required_feature(self):
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        self.clear_recheck_reports()
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("缺剃刀复审", result.stdout)
        for feature_id in ("root", "alpha", "beta"):
            self.assertIn(feature_id, result.stdout)

    def test_exact_recheck_coverage_accepts_only_zero_reduction_with_refactor(self):
        main, razor = self.recheck_scenario()
        self.write_reports(main, razor)
        self.write_recheck_report(recheck_report(self.repository, ["alpha"]))
        result = self.run_check(
            reports=(str(self.main_path), str(self.razor_path), str(self.recheck_path))
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_recheck_rejects_an_extra_feature_that_needs_no_recheck(self):
        main, razor = self.recheck_scenario()
        self.write_reports(main, razor)
        self.write_recheck_reports([
            recheck_report(self.repository, ["alpha"]),
            recheck_report(self.repository, ["beta"]),
        ])
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("不用复审", result.stdout)
        self.assertIn("beta", result.stdout)

    def test_recheck_rejects_duplicate_required_feature(self):
        main, razor = self.recheck_scenario()
        self.write_reports(main, razor)
        self.write_recheck_reports([
            recheck_report(self.repository, ["alpha"]),
            recheck_report(self.repository, ["alpha"]),
        ])
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("剃刀复审重复功能 alpha 2 次", result.stdout)

    def test_empty_recheck_report_is_rejected_when_nothing_needs_rechecking(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        for item in razor["features"]:
            removal = finding("删除", False, "直接做")
            removal["target"] = item["feature"]
            item["keep"] = []
            item["findings"] = [removal]
        self.write_reports(main, razor)
        self.write_recheck_report(recheck_report(self.repository, []))
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("不用复审", result.stdout)

    def test_draft_reason_does_not_require_or_allow_recheck(self):
        map_path = self.repository / "repo-map" / "features.json"
        feature_map = json.loads(map_path.read_text(encoding="utf-8"))
        alpha = feature_map["children"][0]
        alpha["why"] = {"text": "理由待确认。", "source": "起草", "quote": "", "from": ""}
        self.add_tracked_file(
            "repo-map/features.json", json.dumps(feature_map, ensure_ascii=False)
        )
        main, razor = self.recheck_scenario()
        for item in razor["features"]:
            if item["feature"] == "alpha":
                item["keep"] = []
                item["findings"] = []
        self.write_reports(main, razor)
        self.write_recheck_report(recheck_report(self.repository, ["alpha"]))
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("不用复审", result.stdout)
        self.assertIn("alpha", result.stdout)

    def test_feature_with_no_keep_files_does_not_require_recheck(self):
        self.add_empty_feature()
        main = report("主审", self.repository, ("root", "alpha", "beta", "empty"))
        razor = report("剃刀", self.repository, ("root", "alpha", "beta", "empty"))
        rechecks = [
            recheck_report(self.repository, [feature_id])
            for feature_id in ("root", "alpha", "beta", "empty")
        ]
        self.write_reports(main, razor)
        self.write_recheck_reports(rechecks)
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("不用复审", result.stdout)
        self.assertIn("empty", result.stdout)

    def test_recheck_deletion_must_be_asked_even_when_behavior_does_not_change(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        item = finding("删除", False, "直接做")
        item["target"] = "alpha/main.py:1"
        recheck = recheck_report(self.repository, ["alpha"])
        recheck["features"][0]["findings"] = [item]
        self.write_reports(main, razor)
        self.write_recheck_report(recheck)
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("剃刀复审的决定必须是 问你", result.stdout)

    def test_recheck_feature_rejects_razor_only_fields(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        recheck = recheck_report(self.repository, ["alpha"])
        recheck["features"][0]["keep"] = []
        self.write_reports(main, razor)
        self.write_recheck_report(recheck)
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("有多余字段 keep", result.stdout)

    def test_recheck_requires_a_verdict_for_every_keep(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        self.write_reports(main, razor)
        rechecks = self.read_recheck_reports()
        recheck = next(
            document for document in rechecks
            if document["features"][0]["feature"] == "alpha"
        )
        alpha = next(item for item in recheck["features"] if item["feature"] == "alpha")
        alpha["verdicts"] = []
        self.write_recheck_reports(rechecks)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/main.py:1-1", result.stdout)

    def test_recheck_rejects_a_verdict_for_lines_not_in_keep(self):
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        recheck = self.read_recheck_for_feature("alpha")
        alpha = recheck["features"][0]
        alpha["verdicts"][0]["lines"] = "alpha/main.py:2-2"
        self.write_recheck_report(recheck)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/main.py:2-2", result.stdout)

    def test_recheck_rejects_duplicate_verdicts_for_a_keep(self):
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        recheck = self.read_recheck_for_feature("alpha")
        alpha = recheck["features"][0]
        alpha["verdicts"].append(dict(alpha["verdicts"][0]))
        self.write_recheck_report(recheck)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("重复", result.stdout)
        self.assertIn("alpha/main.py:1-1", result.stdout)

    def test_recheck_rejects_empty_attempt(self):
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        recheck = self.read_recheck_for_feature("alpha")
        alpha = recheck["features"][0]
        alpha["verdicts"][0]["attempt"] = "  "
        self.write_recheck_report(recheck)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("attempt", result.stdout)

    def test_recheck_rejects_unknown_verdict_value(self):
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        recheck = self.read_recheck_for_feature("alpha")
        alpha = recheck["features"][0]
        alpha["verdicts"][0]["verdict"] = "待定"
        self.write_recheck_report(recheck)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("verdict 必须是 站得住 或 站不住", result.stdout)
        self.assertIn("待定", result.stdout)

    def test_recheck_verdict_requires_attempt(self):
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        recheck = self.read_recheck_for_feature("alpha")
        alpha = recheck["features"][0]
        alpha["verdicts"][0].pop("attempt")
        self.write_recheck_report(recheck)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("缺少字段 attempt", result.stdout)

    def test_recheck_verdict_requires_evidence(self):
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        recheck = self.read_recheck_for_feature("alpha")
        alpha = recheck["features"][0]
        alpha["verdicts"][0].pop("evidence")
        self.write_recheck_report(recheck)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("缺少字段 evidence", result.stdout)

    def prepare_two_required_rechecks(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        root = next(item for item in razor["features"] if item["feature"] == "root")
        root["keep"] = []
        deletion = finding("删除", False, "直接做")
        deletion["target"] = "root"
        root["findings"] = [deletion]
        for feature_id in ("alpha", "beta"):
            item = next(entry for entry in razor["features"] if entry["feature"] == feature_id)
            change = finding("重构", True, "问你")
            change["target"] = f"{feature_id}/main.py:1"
            item["findings"] = [change]
        self.write_reports(main, razor)

    def test_two_recheck_reports_accept_one_feature_each(self):
        self.prepare_two_required_rechecks()
        self.write_recheck_reports([
            recheck_report(self.repository, ["alpha"]),
            recheck_report(self.repository, ["beta"]),
        ])
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_one_recheck_report_rejects_feature_counts_other_than_one(self):
        for feature_ids in ([], ["alpha", "beta"]):
            with self.subTest(feature_ids=feature_ids):
                if len(feature_ids) == 2:
                    self.prepare_two_required_rechecks()
                else:
                    self.write_reports(report("主审", self.repository),
                                       report("剃刀", self.repository))
                self.write_recheck_report(recheck_report(self.repository, feature_ids))
                result = self.run_check(reports=self.reports_for_check())
                self.assertEqual(result.returncode, 1)
                self.assertIn("features 必须恰有一个功能对象", result.stdout)

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

    def test_feature_id_refactor_does_not_cover_all_keep_files(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = []
        change = finding("重构", True, "问你")
        change["target"] = "alpha"
        alpha["findings"] = [change]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("未覆盖功能文件", result.stdout)
        self.assertIn("alpha/main.py:1", result.stdout)

    def test_out_of_scope_deletion_does_not_suppress_zero_reduction_recheck(self):
        for target in ("beta/main.py:1", "beta"):
            with self.subTest(target=target):
                main = report("主审", self.repository)
                razor = report("剃刀", self.repository)
                alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
                alpha["keep"] = []
                deletion = finding("删除", False, "直接做")
                deletion["target"] = target
                alpha["findings"] = [deletion]
                self.write_reports(main, razor)
                self.clear_recheck_reports()
                result = self.run_check()
                self.assertEqual(result.returncode, 1)
                self.assertIn("缺剃刀复审：alpha", result.stdout)

    def test_recheck_finding_rejects_out_of_scope_file_target(self):
        main, razor = self.recheck_scenario()
        self.write_reports(main, razor)
        recheck = self.read_recheck_for_feature("alpha")
        item = finding("重构", True, "问你")
        item["target"] = "beta/main.py:1"
        recheck["features"][0]["findings"] = [item]
        self.write_recheck_report(recheck)
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("复审发现目标必须在本功能", result.stdout)

    def test_recheck_finding_rejects_other_feature_id_target(self):
        main, razor = self.recheck_scenario()
        self.write_reports(main, razor)
        recheck = self.read_recheck_for_feature("alpha")
        item = finding("重构", True, "问你")
        item["target"] = "beta"
        recheck["features"][0]["findings"] = [item]
        self.write_recheck_report(recheck)
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("复审发现目标必须在本功能", result.stdout)

    def test_recheck_rejects_non_string_feature_id_without_crashing(self):
        main, razor = self.recheck_scenario()
        self.write_reports(main, razor)
        recheck = self.read_recheck_for_feature("alpha")
        recheck["features"][0]["feature"] = ["alpha"]
        self.write_recheck_report(recheck)
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("不在功能清单中", result.stdout)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_evidence_accepts_sentence_punctuation_but_not_decimal_suffix(self):
        main, razor = self.recheck_scenario()
        self.write_reports(main, razor)
        recheck = self.read_recheck_for_feature("alpha")
        alpha = recheck["features"][0]
        alpha["verdicts"][0]["evidence"] = "结果见 README.md:1."
        self.write_recheck_report(recheck)
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        recheck = self.read_recheck_for_feature("alpha")
        recheck["features"][0]["verdicts"][0]["evidence"] = "小数 README.md:1.5 不作为行号。"
        self.write_recheck_report(recheck)
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("evidence 必须引至少一处有效位置", result.stdout)

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

    def test_one_python_keep_cannot_cross_a_definition_inside_module_control_flow(self):
        self.add_tracked_file(
            "alpha/conditional.py",
            "if flag:\n    def first():\n        return 1\ndef second():\n    return 2\n",
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"].append(razor_keep_item("alpha", "alpha/conditional.py:1-5"))
        self.write_reports(main, razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("alpha/conditional.py:1-5", result.stdout)
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

    def test_keep_cannot_cross_markdown_headings(self):
        cases = (
            ("indented-headings.md", " # One\n  # Two\n   # Three\n", "1-3"),
            ("blockquote-headings.md", "> # A\n> # B\n", "1-2"),
            ("list-headings.md", "- # A\n- # B\n", "1-2"),
            ("setext-headings.md", "First\n===\nMiddle\nSecond\n---\n", "1-5"),
            ("quoted-setext.md", "> First\n> ===\n> Second\n> ---\n", "1-4"),
            ("list-setext.md", "- First\n  ===\n- Second\n  ---\n", "1-4"),
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        for filename, content, lines in cases:
            relative_path = f"alpha/{filename}"
            self.add_tracked_file(relative_path, content)
            alpha["keep"].append(
                razor_keep_item("alpha", f"{relative_path}:{lines}")
            )
        self.write_reports(main, razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for filename, _, lines in cases:
            self.assertIn(f"alpha/{filename}:{lines}", result.stdout)
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

    def test_unclosed_yaml_delimiter_does_not_hide_later_headings(self):
        self.add_tracked_file(
            "alpha/unclosed-front-matter.md",
            "---\n# First\n## Second\n",
        )
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"].append(
            razor_keep_item("alpha", "alpha/unclosed-front-matter.md:1-3")
        )
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/unclosed-front-matter.md:1-3", result.stdout)
        self.assertIn("一个实体", result.stdout)

    def test_code_blocks_list_items_and_table_separators_are_not_headings(self):
        headings = check_reports_module._markdown_heading_lines([
            "A | B",
            "---",
            "| left | right |",
            "| --- | --- |",
        ])
        self.assertEqual({1}, headings)
        headings = check_reports_module._markdown_heading_lines([
            "- list item",
            "---",
            "> quote text",
            "---",
            "> Quoted title",
            "> ===",
            "- List title",
            "  ---",
        ])
        self.assertEqual({5, 7}, headings)
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
            ("主审", "necessity", "功能必要。"),
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
        recorded = subprocess.run(
            ["python3", "-B", str(ROOT / "xy-cleanup" / "approvals.py"),
             "record", "--repo", str(self.repository), "--scope", "全部"],
            capture_output=True, text=True, check=False,
        )
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
        result = self.run_check(reports=(
            str(self.main_path), str(second_main), str(self.razor_path),
            *(str(path) for path in self.recheck_paths),
        ))
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

    def test_non_string_role_is_rejected_without_crashing(self):
        main = report("主审", self.repository)
        main["role"] = []
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        output = result.stdout + result.stderr
        self.assertIn("role 必须是", output)
        self.assertNotIn("Traceback", output)

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
