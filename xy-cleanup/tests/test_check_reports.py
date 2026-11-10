"""Test the public validator for per-feature cleanup review reports."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "xy-repo-map" / "tests"))

from map_fixture import feature, make_repo  # noqa: E402


COMMAND = ROOT / "xy-cleanup" / "check_reports.py"
RULE_SOURCE = "xy-iron-law 第 3 条"
RULE_TEXT = "如无必要，勿增实体（奥卡姆剃刀）。"


def review_feature(feature_id):
    return {"feature": feature_id, "what_true": "实现与说明相符。",
            "needed": "需要。", "smaller": "不能更少。",
            "why_holds": "理由仍成立。", "findings": []}


def report(role, repository, feature_ids=("root", "alpha", "beta")):
    return {"role": role, "repo": str(repository),
            "features": [review_feature(feature_id) for feature_id in feature_ids]}


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


class CheckReportsTest(unittest.TestCase):
    def setUp(self):
        self.repository = make_repo(self, {
            "README.md": "Demo.\n",
            "alpha/main.py": "value = 1\n",
            "beta/main.py": "value = 2\n",
        }, feature("root", "", children=[
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

    def test_accepts_complete_reports_for_each_role(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_requires_exact_top_level_and_feature_fields(self):
        main = report("主审", self.repository)
        main["legacy"] = "unused"
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("legacy", result.stdout)

        main = report("主审", self.repository)
        del main["features"][0]["needed"]
        self.write_reports(main, report("剃刀", self.repository))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("needed", result.stdout)

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

        main["features"].insert(0, review_feature("root"))
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

    def test_finding_target_must_be_a_tracked_feature_or_valid_file_line(self):
        main = report("主审", self.repository)
        main["features"][0]["findings"] = [finding()]
        self.write_reports(main, report("剃刀", self.repository))
        self.assertEqual(self.run_check().returncode, 0)

        invalid_targets = (
            "alpha/main.py:2",
            "missing.py:1",
            "alpha/main.py:0",
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
                    "features[0].findings[0].target 必须是功能 id 或 文件:行号："
                    + target,
                    result.stdout,
                )

        main = report("主审", self.repository)
        feature_target = finding()
        feature_target["target"] = "alpha"
        main["features"][0]["findings"] = [feature_target]
        self.write_reports(main, report("剃刀", self.repository))
        self.assertEqual(self.run_check().returncode, 0)

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
        result = self.run_check(("missing",))
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
