import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REVIEW_DIRECTORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REVIEW_DIRECTORY / "review_report_check"))
sys.path.insert(0, str(REVIEW_DIRECTORY / "rule_quote_check"))
import rule_references
import check
from check import REPO_RULE_NUMBERS, main


CLEANUP_CATEGORIES = ["多余的", "重复的", "臃肿的", "命名", "测试", "注释和文档", "配置和依赖"]
SCANNER = REVIEW_DIRECTORY / "change_scan" / "scan_changes.py"


def write_file(directory, name, content):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, name), "w", encoding="utf-8") as stream:
        stream.write(content)


def section(items=None, checked="核对了实现与 spec"):
    return {"items": [] if items is None else items, "checked": checked}


def main_report(verdict="通过", deviations=None, hard_to_misuse=None, repo_rules=None):
    return {
        "first_principles": {
            "deviations": section(deviations),
            "quote_problems": section(),
        },
        "hard_to_misuse": section(hard_to_misuse),
        "repo_rules": {str(number): section() for number in REPO_RULE_NUMBERS} if repo_rules is None else repo_rules,
        "mapping": {
            "items": [{"block": "主审报告", "need": "报告必须能核对用户要求"}],
            "merges": [],
        },
        "verdict": verdict,
    }


def razor_report(verdict="通过", keep=None, keep_old=None, delete=None, ask_user=None):
    return {
        "occams_razor": {
            "keep": [{
                "entity": "check.py",
                "requirement": "用户原话 1「检查改动」",
                "achieved": "检查改动是否符合要求",
                "cost": "实现简单，代价配得上收益",
                "user_visible": {"change": "不改"},
            }] if keep is None else keep,
            "keep_old": [] if keep_old is None else keep_old,
            "delete": {"items": [] if delete is None else delete, "checked": "核对了依赖与调用方"},
            "ask_user": {"items": [] if ask_user is None else ask_user, "checked": "核对了行为变化"},
            "quote_problems": section(),
        },
        "verdict": verdict,
    }


def derivation_report(root="根在遗漏最少要求。", needs=None, approach="按最少要求逐项对照对象。"):
    return {
        "root": root,
        "needs": [{
            "need": "报告必须能核对用户要求",
            "quote": {"source": "Q1", "text": "按要求审查"},
        }] if needs is None else needs,
        "approach": approach,
    }


def diagnosis_report(diagnosis="问题在于目标被拆成互相矛盾的两种格式。", actions=None):
    return {"diagnosis": diagnosis, "actions": ["重想"] if actions is None else actions}


def cleanup_section(hit_ids=(), decision="留"):
    return {
        "hits": [
            {"id": hit_id, "decision": decision, "reason": "保留的写法满足现有调用要求"}
            for hit_id in hit_ids
        ],
        "categories": [
            {
                "category": category,
                "looked_at": "检查了改动文件及其相关调用和测试",
                "conclusion": "没有发现需要处理的内容",
            }
            for category in CLEANUP_CATEGORIES
        ],
    }


class CheckTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name

    def run_main(self, path_parts, text, create_derivation=True):
        path = os.path.join(self.root, *path_parts)
        write_file(os.path.dirname(path), os.path.basename(path), text)
        if create_derivation and path_parts[-1] in ("review-main.json", "review-razor.json"):
            derivation_name = "derivation-main.json" if path_parts[-1] == "review-main.json" else "derivation-razor.json"
            write_file(
                os.path.dirname(path), derivation_name,
                json.dumps(derivation_report(), ensure_ascii=False),
            )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["check.py", path])
        return code, output.getvalue().splitlines()

    def run_report(self, name, document, folders=("group1", "round1"), create_derivation=True):
        return self.run_main(
            (*folders, name), json.dumps(document, ensure_ascii=False), create_derivation
        )

    def create_formal_round3(self, failed=False, group="group1"):
        group_path = Path(self.root) / group
        for number in (1, 2):
            (group_path / f"round{number}").mkdir(parents=True, exist_ok=True)
        reports = [
            ("review-main.json", main_report()),
            ("review-razor.json", razor_report()),
        ]
        if failed:
            reports[0] = ("review-main.json", main_report(verdict="不通过", hard_to_misuse=[{
                "problem": "缺陷",
                "evidence": "check.py:5",
                "quote": {"source": "xy-repo-rules 第 1 条", "text": "人类或 agent 读完 README.md 后都能立刻上手。"},
            }]))
        for name, report in reports:
            code, output = self.run_report(name, report, (group, "round3"))
            self.assertEqual(code, 0, output)

    def create_complete_formal_group(self, failed, group):
        self.create_formal_round3(failed=failed, group=group)
        group_path = Path(self.root) / group
        for number in (1, 2):
            round_path = group_path / f"round{number}"
            write_file(round_path, "review-main.json", json.dumps(main_report(), ensure_ascii=False))
            write_file(round_path, "review-razor.json", json.dumps(razor_report(), ensure_ascii=False))
            write_file(round_path, "derivation-main.json", json.dumps(derivation_report(), ensure_ascii=False))
            write_file(round_path, "derivation-razor.json", json.dumps(derivation_report(), ensure_ascii=False))
        for number in (1, 2, 3):
            write_file(group_path / f"round{number}", "submitted-review.md", "本轮审查版本。\n")
        if failed:
            write_file(group_path / "round3", "handling.md", "逐项处理了不通过意见。\n")

    def assert_rejected(self, name, document, *needles, folders=("group1", "round1")):
        code, problems = self.run_report(name, document, folders)
        self.assertEqual(code, 1, problems)
        for needle in needles:
            self.assertTrue(any(needle in problem for problem in problems), f"没报「{needle}」：{problems}")

    def make_code_submission(self):
        repository = Path(self.root) / "reviewed-repository"
        repository.mkdir()

        def git(*arguments):
            return subprocess.run(
                ["git", "-C", str(repository), *arguments],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        git("init", "--quiet")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.com")
        lines = [f"shared line {number} includes enough alphanumeric words" for number in range(1, 7)]
        write_file(repository, "source.md", "\n".join(lines) + "\n")
        git("add", "-A")
        git("commit", "--quiet", "-m", "base")
        base = git("rev-parse", "HEAD")
        write_file(repository, "copy.md", "\n".join(lines) + "\n")
        git("add", "-A")
        git("commit", "--quiet", "-m", "copy")
        result = subprocess.run(
            [sys.executable, "-B", str(SCANNER), "--repo", str(repository), "--base", base],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return repository, base, json.loads(result.stdout)

    def run_code_razor(self, document, scan_document):
        scan_path = Path(self.root) / "code" / "group1" / "round1"
        write_file(
            scan_path,
            "submitted-scan.json",
            json.dumps(scan_document, ensure_ascii=False),
        )
        return self.run_main(
            ("code", "group1", "round1", "review-razor.json"),
            json.dumps(document, ensure_ascii=False),
        )

    def clean_code_razor_report(self, scan_document, decision="留"):
        report = razor_report()
        report["cleanup"] = cleanup_section(
            [hit["id"] for hit in scan_document["hits"]], decision
        )
        return report

    def test_code_razor_requires_cleanup_and_submitted_scan_format(self):
        _repository, _base, scan_document = self.make_code_submission()
        report = razor_report()

        code, output = self.run_code_razor(report, scan_document)

        self.assertEqual(code, 1, output)
        self.assertTrue(any("cleanup" in problem and "缺少字段" in problem for problem in output), output)

    def test_valid_code_razor_replies_to_each_scanned_hit_and_category(self):
        _repository, _base, scan_document = self.make_code_submission()

        code, output = self.run_code_razor(self.clean_code_razor_report(scan_document), scan_document)

        self.assertEqual((code, output), (0, []))

    def test_cleanup_hit_ids_must_match_the_scan_without_missing_extra_or_duplicates(self):
        _repository, _base, scan_document = self.make_code_submission()
        expected_id = scan_document["hits"][0]["id"]
        cases = [([], "缺少"), ([expected_id, "extra-id"], "多余"), ([expected_id, expected_id], "重复")]

        for ids, problem in cases:
            with self.subTest(ids=ids):
                report = self.clean_code_razor_report(scan_document)
                report["cleanup"]["hits"] = [
                    {"id": hit_id, "decision": "留", "reason": "理由清楚且针对该命中"}
                    for hit_id in ids
                ]
                code, output = self.run_code_razor(report, scan_document)
                self.assertEqual(code, 1, output)
                self.assertTrue(any(problem in item for item in output), output)

    def test_cleanup_decisions_and_reasons_are_required(self):
        _repository, _base, scan_document = self.make_code_submission()
        report = self.clean_code_razor_report(scan_document)
        report["cleanup"]["hits"][0]["decision"] = "maybe"
        report["cleanup"]["hits"][0]["reason"] = "   "

        code, output = self.run_code_razor(report, scan_document)

        self.assertEqual(code, 1, output)
        self.assertTrue(any("decision" in item and "只能是" in item for item in output), output)
        self.assertTrue(any("reason 不能为空" in item for item in output), output)

    def test_cleanup_categories_must_have_each_workflow_class_once(self):
        _repository, _base, scan_document = self.make_code_submission()
        cases = [
            (CLEANUP_CATEGORIES[:-1], "缺少"),
            (CLEANUP_CATEGORIES + ["新人类"], "多余"),
            (CLEANUP_CATEGORIES + [CLEANUP_CATEGORIES[0]], "重复"),
        ]

        for categories, problem in cases:
            with self.subTest(categories=categories):
                report = self.clean_code_razor_report(scan_document)
                rows = report["cleanup"]["categories"]
                report["cleanup"]["categories"] = [
                    next(row for row in rows if row["category"] == category)
                    if category in CLEANUP_CATEGORIES else {
                        "category": category,
                        "looked_at": "检查了相关改动",
                        "conclusion": "没有发现",
                    }
                    for category in categories
                ]
                if problem == "重复":
                    report["cleanup"]["categories"][-1] = dict(rows[0])
                code, output = self.run_code_razor(report, scan_document)
                self.assertEqual(code, 1, output)
                self.assertTrue(any("cleanup.categories" in item for item in output), output)

    def test_cleanup_categories_follow_the_workflow_table(self):
        path = Path(self.root) / "workflow.md"
        write_file(
            path.parent,
            path.name,
            "   | 类 | 包括但不限于 |\n   |---|---|\n   | 新类别 | 一个例子 |\n",
        )

        with patch.object(check, "WORKFLOW_SKILL_PATH", path):
            categories = check.workflow_cleanup_categories()

        self.assertEqual(categories, ["新类别"])

    def test_cleanup_is_rejected_outside_the_code_review_path(self):
        _repository, _base, scan_document = self.make_code_submission()
        report = razor_report()
        report["cleanup"] = cleanup_section(
            [hit["id"] for hit in scan_document["hits"]]
        )

        code, output = self.run_report(
            "review-razor.json", report, folders=("spec", "group1", "round1")
        )

        self.assertEqual(code, 1, output)
        self.assertTrue(any("cleanup" in item and "代码审查" in item for item in output), output)

    def test_code_razor_requires_submitted_scan_json(self):
        scan_path = Path(self.root) / "code" / "group1" / "round1"
        report = self.clean_code_razor_report({"hits": []})
        path = scan_path / "review-razor.json"
        write_file(path.parent, path.name, json.dumps(report, ensure_ascii=False))

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["check.py", str(path)])

        self.assertEqual(code, 1)
        self.assertIn("submitted-scan.json", output.getvalue())

    def test_code_razor_rejects_a_submitted_scan_that_does_not_match_the_rescan(self):
        _repository, _base, scan_document = self.make_code_submission()
        altered_scan = json.loads(json.dumps(scan_document))
        altered_scan["hits"][0]["message"] = "被改过的旧消息"
        report = self.clean_code_razor_report(scan_document)

        code, output = self.run_code_razor(report, altered_scan)

        self.assertEqual(code, 1, output)
        self.assertTrue(any("submitted-scan.json" in item and "重跑" in item for item in output), output)

    def test_submitted_scan_rejects_bad_shape_and_stale_output(self):
        _repository, _base, scan_document = self.make_code_submission()
        cases = [
            ([], "格式"),
            ({**scan_document, "repo": 1}, "格式"),
            ({**scan_document, "base": None}, "格式"),
            ({key: value for key, value in scan_document.items() if key != "head"}, "重跑"),
            ({**scan_document, "head": "not-a-commit-id"}, "重跑"),
        ]

        for submitted, expected_error in cases:
            with self.subTest(expected_error=expected_error, submitted=submitted):
                report = self.clean_code_razor_report(scan_document)
                code, output = self.run_code_razor(report, submitted)
                self.assertEqual(code, 1, output)
                self.assertTrue(
                    any(
                        "submitted-scan.json" in item and expected_error in item
                        for item in output
                    ),
                    output,
                )

    def test_cleanup_hit_ids_must_be_strings(self):
        _repository, _base, scan_document = self.make_code_submission()
        report = self.clean_code_razor_report(scan_document)
        report["cleanup"]["hits"][0]["id"] = {"not": "an id"}

        code, output = self.run_code_razor(report, scan_document)

        self.assertEqual(code, 1, output)
        self.assertTrue(any("hits[0].id 必须是字符串" in item for item in output), output)

    def test_code_razor_rejects_a_repository_head_different_from_the_submission(self):
        repository, _base, scan_document = self.make_code_submission()
        later_commit = Path(repository) / "later.md"
        later_commit.write_text("a newer HEAD\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "commit", "--quiet", "-m", "later"],
            check=True,
        )

        code, output = self.run_code_razor(
            self.clean_code_razor_report(scan_document), scan_document
        )

        self.assertEqual(code, 1, output)
        self.assertTrue(any("head" in item and "HEAD 不一致" in item for item in output), output)

    def test_cleanup_hit_changes_cannot_pass_and_can_pass_after_author_change(self):
        _repository, _base, scan_document = self.make_code_submission()
        changed_report = self.clean_code_razor_report(scan_document, decision="改")
        self.assertEqual(changed_report["verdict"], "通过")

        code, output = self.run_code_razor(changed_report, scan_document)

        self.assertEqual(code, 1, output)
        self.assertTrue(any("cleanup.hits" in item and "通过" in item for item in output), output)

        changed_report["verdict"] = "改了就过"
        code, output = self.run_code_razor(changed_report, scan_document)
        self.assertEqual((code, output), (0, []))

    def test_report_with_only_a_change_decision_may_use_changed_verdict_without_delete(self):
        _repository, _base, scan_document = self.make_code_submission()
        report = self.clean_code_razor_report(scan_document, decision="改")
        report["verdict"] = "改了就过"
        report["occams_razor"]["keep"] = []
        report["occams_razor"]["keep_old"] = []

        code, output = self.run_code_razor(report, scan_document)

        self.assertEqual((code, output), (0, []))

    def test_historical_code_reports_are_not_rescanned_after_repository_head_changes(self):
        repository, _base, old_scan = self.make_code_submission()
        git = lambda *arguments: subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        write_file(repository, "later.md", "a later commit\n")
        git("add", "-A")
        git("commit", "--quiet", "-m", "later")
        group_path = Path(self.root) / "code" / "group1"

        for number in (1, 2, 3):
            round_path = group_path / f"round{number}"
            razor = self.clean_code_razor_report(old_scan)
            if number == 3:
                razor["verdict"] = "不通过"
                razor["occams_razor"]["delete"]["items"] = [
                    {"entity": "unused.md", "loss": "失去旧说明"}
                ]
                write_file(round_path, "handling.md", "记录了第 3 轮意见的处理。\n")
            write_file(round_path, "review-main.json", json.dumps(main_report(), ensure_ascii=False))
            write_file(round_path, "review-razor.json", json.dumps(razor, ensure_ascii=False))
            write_file(round_path, "derivation-main.json", json.dumps(derivation_report(), ensure_ascii=False))
            write_file(round_path, "derivation-razor.json", json.dumps(derivation_report(), ensure_ascii=False))
            write_file(
                round_path,
                "submitted-scan.json",
                json.dumps(old_scan, ensure_ascii=False),
            )

        diagnosis_path = group_path / "diagnosis.json"
        write_file(
            group_path,
            diagnosis_path.name,
            json.dumps(diagnosis_report(), ensure_ascii=False),
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["check.py", str(diagnosis_path)])

        self.assertEqual(code, 0, output.getvalue())

    def test_clean_json_reports_pass(self):
        self.assertEqual(self.run_report("review-main.json", main_report()), (0, []))
        self.assertEqual(self.run_report("review-razor.json", razor_report()), (0, []))
        self.assertEqual(self.run_report("derivation-main.json", derivation_report()), (0, []))
        self.assertEqual(self.run_report("derivation-razor.json", derivation_report()), (0, []))

    def test_main_report_requires_exact_keys_and_types(self):
        cases = []
        document = main_report()
        del document["verdict"]
        cases.append((document, "缺少字段：报告.verdict"))

        document = main_report()
        document["extra"] = "多余"
        cases.append((document, "多余字段：extra"))

        document = main_report()
        document["first_principles"]["deviations"] = {"items": [], "checked": "对照"}
        document["first_principles"]["deviations"]["extra"] = "多余"
        cases.append((document, "first_principles.deviations 多余字段：extra"))

        document = main_report()
        document["hard_to_misuse"]["items"] = "无"
        cases.append((document, "报告.hard_to_misuse.items 必须是数组"))

        document = main_report()
        document["first_principles"]["deviations"]["items"] = [{
            "problem": "问题",
            "quote": {"source": "Q94", "text": "review的结果…是否用json更好？"},
        }]
        cases.append((document, "缺少字段：报告.first_principles.deviations.items[0].evidence"))

        document = main_report()
        document["first_principles"]["essence"] = "多余字段"
        cases.append((document, "first_principles 多余字段：essence"))

        document = main_report()
        document["hard_to_misuse"]["checked"] = []
        cases.append((document, "报告.hard_to_misuse.checked 必须是字符串"))

        for document, message in cases:
            with self.subTest(message=message):
                self.assert_rejected("review-main.json", document, message)

    def test_main_report_requires_quote_problems(self):
        document = main_report()
        del document["first_principles"]["quote_problems"]
        self.assert_rejected(
            "review-main.json", document,
            "缺少字段：报告.first_principles.quote_problems",
        )

    def test_quote_problems_require_suggestion(self):
        finding = {
            "problem": "两条原话互相矛盾",
            "evidence": "与原话 1「必须启用」相矛盾",
            "quote": {"source": "Q2", "text": "默认关闭"},
        }
        document = main_report(verdict="改了就过")
        document["first_principles"]["quote_problems"]["items"] = [finding]
        self.assert_rejected(
            "review-main.json", document,
            "缺少字段：报告.first_principles.quote_problems.items[0].suggestion",
        )

    def test_quote_problems_are_main_verdict_findings(self):
        finding = {
            "problem": "两条原话互相矛盾",
            "evidence": "与原话 1「必须启用」相矛盾",
            "quote": {"source": "Q2", "text": "默认关闭"},
            "suggestion": "请用户决定是否启用，并说明理由",
        }
        document = main_report()
        document["first_principles"]["quote_problems"]["items"] = [finding]
        self.assert_rejected(
            "review-main.json", document,
            "通过」但有发现", "first_principles.quote_problems",
        )
        document = main_report(verdict="改了就过")
        document["first_principles"]["quote_problems"]["items"] = [finding]
        self.assertEqual(self.run_report("review-main.json", document), (0, []))

    def test_main_report_items_require_nonblank_quotes(self):
        def finding():
            return {
                "problem": "发现一项阻断",
                "evidence": "check.py:25",
                "quote": {"source": "Q94", "text": "review的结果…是否用json更好？"},
            }

        cases = []
        item = finding()
        del item["quote"]
        cases.append((item, "缺少字段：报告.first_principles.deviations.items[0].quote"))

        item = finding()
        del item["quote"]["source"]
        cases.append((item, "缺少字段：报告.first_principles.deviations.items[0].quote.source"))

        item = finding()
        del item["quote"]["text"]
        cases.append((item, "缺少字段：报告.first_principles.deviations.items[0].quote.text"))

        item = finding()
        item["quote"]["source"] = " \n\t"
        cases.append((item, "报告.first_principles.deviations.items[0].quote.source 不能为空"))

        item = finding()
        item["quote"]["text"] = " \n\t"
        cases.append((item, "报告.first_principles.deviations.items[0].quote.text 不能为空"))

        for item, message in cases:
            with self.subTest(message=message):
                document = main_report(verdict="改了就过", deviations=[item])
                self.assert_rejected("review-main.json", document, message)

        document = main_report(verdict="改了就过", deviations=[finding()])
        self.assertEqual(self.run_report("review-main.json", document), (0, []))

    def test_main_report_rejects_rule_quote_not_found_in_the_named_rule(self):
        finding = {
            "problem": "发现一项阻断",
            "evidence": "check.py:25",
            "quote": {"source": "xy-iron-law 第 1 条", "text": "不是该条原文"},
        }
        document = main_report(verdict="改了就过", deviations=[finding])
        self.assert_rejected("review-main.json", document, "quote.text", "xy-iron-law 第 1 条")

    def test_main_report_checks_rule_quotes_in_quote_problems(self):
        document = main_report(verdict="改了就过")
        document["first_principles"]["quote_problems"]["items"] = [{
            "problem": "规范引文不准确",
            "evidence": "check.py:25",
            "quote": {"source": "xy-repo-rules 第 4 条", "text": "不是第四条原文"},
            "suggestion": "逐字核对",
        }]

        self.assert_rejected(
            "review-main.json", document,
            "first_principles.quote_problems.items[0].quote.text",
            "xy-repo-rules 第 4 条",
        )

    def test_main_report_rejects_rule_name_anywhere_in_source(self):
        sources = (
            "xy-iron-law第 1 条",
            " xy-iron-law 第 1 条",
            "见 xy-iron-law 第 1 条",
            str(rule_references.RULE_FILES["xy-iron-law"]),
        )
        for source in sources:
            with self.subTest(source=source):
                finding = {
                    "problem": "发现一项阻断",
                    "evidence": "check.py:25",
                    "quote": {"source": source, "text": "错误引文"},
                }
                document = main_report(verdict="改了就过", deviations=[finding])
                self.assert_rejected("review-main.json", document,
                                     "格式错误或条号不存在")

    def test_main_report_accepts_contract_number_and_quote_file_path_sources(self):
        sources = ("Q94", "xy-goal/quotes/50.md")
        findings = [
            {
                "problem": "发现一项阻断",
                "evidence": "check.py:25",
                "quote": {"source": source, "text": "原话原文"},
            }
            for source in sources
        ]
        document = main_report(verdict="改了就过", deviations=findings)
        self.assertEqual(self.run_report("review-main.json", document), (0, []))

    def test_check_cannot_build_zero_repository_rule_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing_path = Path(temporary) / "missing-rules.md"
            try:
                with patch.dict(rule_references.RULE_FILES,
                                {"xy-repo-rules": missing_path}):
                    with self.assertRaises(ValueError):
                        importlib.reload(check)
            finally:
                importlib.reload(check)

    def test_main_report_accepts_a_continuous_quote_from_the_named_rule(self):
        quotes = (
            ("xy-iron-law 第 1 条", "第一性原理：思考"),
            ("xy-iron-law 第 3 条", "用户原话、功能清单或有证据的派方需求"),
        )
        for source, text in quotes:
            with self.subTest(source=source):
                finding = {
                    "problem": "发现一项阻断",
                    "evidence": "check.py:25",
                    "quote": {"source": source, "text": text},
                }
                document = main_report(verdict="改了就过", deviations=[finding])
                self.assertEqual(self.run_report("review-main.json", document), (0, []))

    def test_main_report_requires_each_numbered_repository_rule(self):
        rules = {str(number): section() for number in REPO_RULE_NUMBERS}
        missing = str(REPO_RULE_NUMBERS[-1])
        del rules[missing]
        rules[str(REPO_RULE_NUMBERS[-1] + 1)] = section()
        document = main_report(repo_rules=rules)
        self.assert_rejected("review-main.json", document,
                             f"缺少字段：报告.repo_rules.{missing}",
                             f"repo_rules 多余字段：{REPO_RULE_NUMBERS[-1] + 1}")

    def test_main_report_accepts_a_repository_rule_finding(self):
        rules = {str(number): section() for number in REPO_RULE_NUMBERS}
        rules[str(REPO_RULE_NUMBERS[0])] = section([{
            "problem": "README 还不能让新读者立即上手",
            "evidence": "README.md:1",
            "quote": {
                "source": "xy-repo-rules 第 1 条",
                "text": "人类或 agent 读完 README.md 后都能立刻上手。",
            },
        }])
        document = main_report(verdict="改了就过", repo_rules=rules)
        self.assertEqual(self.run_report("review-main.json", document), (0, []))

    def test_main_verdict_matches_findings(self):
        findings = [{
            "problem": "增量：漏掉新约束；用户原话 4「格式规范」",
            "evidence": "check.py:25",
            "quote": {"source": "Q94", "text": "review的结果…是否用json更好？"},
        }]
        self.assertEqual(self.run_report("review-main.json", main_report(verdict="改了就过", deviations=findings)), (0, []))
        self.assert_rejected("review-main.json", main_report(verdict="通过", deviations=findings), "通过」但有发现")
        self.assert_rejected("review-main.json", main_report(verdict="不通过"), "但没有发现")
        self.assert_rejected("review-main.json", main_report(verdict="未知"), "verdict 只能是")

    def test_razor_report_requires_exact_keys_types_and_nonblank_strings(self):
        cases = []
        document = razor_report()
        document["occams_razor"]["delete"]["extra"] = "多余"
        cases.append((document, "occams_razor.delete 多余字段：extra"))

        document = razor_report()
        document["occams_razor"]["keep"] = [{
            "entity": "check.py",
            "requirement": " ",
            "achieved": "检查改动",
            "cost": "代价合适",
        }]
        cases.append((document, "报告.occams_razor.keep[0].requirement 不能为空"))

        document = razor_report()
        document["occams_razor"]["ask_user"]["items"] = [{"entity": "文件"}]
        cases.append((document, "缺少字段：报告.occams_razor.ask_user.items[0].now"))

        document = razor_report()
        document["occams_razor"]["delete"]["checked"] = None
        cases.append((document, "报告.occams_razor.delete.checked 必须是字符串"))

        for document, message in cases:
            with self.subTest(message=message):
                self.assert_rejected("review-razor.json", document, message)

    def test_razor_report_requires_quote_problems(self):
        document = razor_report()
        del document["occams_razor"]["quote_problems"]
        self.assert_rejected(
            "review-razor.json", document,
            "缺少字段：报告.occams_razor.quote_problems",
        )

    def test_razor_quote_problems_reject_invalid_fields_and_quotes(self):
        wrong_keys = razor_report()
        wrong_keys["occams_razor"]["quote_problems"]["items"] = [{
            "problem": "原话把实现细节写成了必须项",
            "evidence": "spec.md:12",
            "quote": {"source": "Q1", "text": "用户原话"},
            "suggested": "建议用户决定",
        }]
        wrong_quote = razor_report()
        wrong_quote["occams_razor"]["quote_problems"]["items"] = [{
            "problem": "原话要求的东西没有必要",
            "evidence": "spec.md:12",
            "quote": {
                "source": "xy-iron-law 第 3 条",
                "text": "删了它，哪个要求做不到",
            },
            "suggestion": "请用户决定是否保留这项要求",
        }]
        cases = [
            (
                wrong_keys,
                (
                    "缺少字段：报告.occams_razor.quote_problems.items[0].suggestion",
                    "多余字段：suggested",
                ),
            ),
            (
                wrong_quote,
                (
                    "报告.occams_razor.quote_problems.items[0].quote.text",
                    "必须逐字引用「xy-iron-law 第 3 条」",
                ),
            ),
        ]
        for document, needles in cases:
            with self.subTest(needles=needles):
                self.assert_rejected("review-razor.json", document, *needles)

    def test_razor_report_may_have_empty_review_lists(self):
        document = razor_report(keep=[], keep_old=[], delete=[], ask_user=[])
        self.assertEqual(self.run_report("review-razor.json", document), (0, []))

    def test_razor_rejects_old_keep_shape(self):
        document = razor_report(keep=[{"entity": "check.py", "reason": "仍在使用"}])
        self.assert_rejected(
            "review-razor.json", document,
            "缺少字段：报告.occams_razor.keep[0].requirement",
            "多余字段：reason",
        )

    def test_razor_rejects_keep_old_with_wrong_keys(self):
        document = razor_report(keep=[], keep_old=[{"entity": "check.py", "why": "入口"}])
        self.assert_rejected(
            "review-razor.json", document,
            "缺少字段：报告.occams_razor.keep_old[0].reason",
            "多余字段：why",
        )

    def test_razor_keep_may_be_empty_when_delete_has_an_item(self):
        deletion = [{"entity": "unused.py", "loss": "失去过时脚本"}]
        document = razor_report(verdict="改了就过", keep=[], delete=deletion)
        self.assertEqual(self.run_report("review-razor.json", document), (0, []))

    def test_razor_verdict_depends_on_delete_items_only(self):
        ask = [{"entity": "option", "now": "三个分支", "change_to": "删除一个", "saving": "少一个分支"}]
        self.assertEqual(self.run_report("review-razor.json", razor_report(ask_user=ask)), (0, []))
        deletion = [{"entity": "unused.py", "loss": "失去过时脚本"}]
        self.assert_rejected("review-razor.json", razor_report(delete=deletion), "通过」但 delete.items")
        self.assert_rejected("review-razor.json", razor_report(verdict="不通过"), "但 delete.items 为空")

    def test_diagnosis_report_validates_structure_and_actions(self):
        self.create_complete_formal_group(failed=True, group="group1")
        self.assertEqual(self.run_report("diagnosis.json", diagnosis_report(), folders=("group1",)), (0, []))
        self.assert_rejected("diagnosis.json", diagnosis_report(actions=[]), "actions 不能为空", folders=("group1",))
        self.assert_rejected("diagnosis.json", diagnosis_report(actions=["拆分", "拆分"]), "actions 不能重复",
                             folders=("group1",))
        self.assert_rejected("diagnosis.json", diagnosis_report(actions=["继续"]), "actions 只能包含",
                             folders=("group1",))
        self.assert_rejected("diagnosis.json", diagnosis_report(diagnosis=" \t\n"), "diagnosis 不能为空",
                             folders=("group1",))

    def test_informal_diagnosis_uses_the_shared_report_validation(self):
        directory = Path(self.root) / "informal-loop"
        write_file(directory, "report-round4.md", "改测记录")
        self.assertEqual(self.run_report("diagnosis.json", diagnosis_report(),
                                         folders=("informal-loop",)), (0, []))
        self.assert_rejected("diagnosis.json", diagnosis_report(actions=[]), "actions 不能为空",
                             folders=("informal-loop",))

    def test_informal_diagnosis_requires_a_valid_report_round_in_its_directory(self):
        directory = Path(self.root) / "informal-loop"
        write_file(directory, "report-round04.md", "不是合法轮次")
        code, problems = self.run_report("diagnosis.json", diagnosis_report(),
                                         folders=("informal-loop",))
        self.assertEqual(code, 1, problems)
        self.assertTrue(any("判因报告必须写在" in problem for problem in problems), problems)

    def test_reports_reject_template_placeholders_and_placeholder_fragments(self):
        document = main_report()
        document["first_principles"]["deviations"]["checked"] = "<对照了什么>"
        self.assert_rejected("review-main.json", document, "模板占位符")

        document = derivation_report(
            root="<只看上游：要解决的根本问题；修错时写错在哪、根源在哪>"
        )
        self.assert_rejected("derivation-main.json", document, "模板占位符")

        self.assert_rejected("diagnosis.json", diagnosis_report(diagnosis="<这组为什么没过，引用各轮报告的阻断项和证据>"),
                             "模板占位符", folders=("group1",))

    def test_diagnosis_template_actions_are_rejected_when_copied(self):
        skill = (REVIEW_DIRECTORY / "SKILL.md").read_text(encoding="utf-8")
        templates = check._report_templates(skill)
        diagnosis_template = templates["diagnosis.json"]
        copied_actions = {"diagnosis": "引用阻断项和证据说明根因。", "actions": diagnosis_template["actions"]}
        self.assert_rejected("diagnosis.json", copied_actions, "模板占位符", folders=("group1",))

    def test_duplicate_json_keys_are_rejected(self):
        text = '{"verdict":"通过","verdict":"不通过"}'
        code, problems = self.run_main(("group1", "round1", "review-main.json"), text)
        self.assertEqual(code, 1)
        self.assertTrue(any("重复键" in problem and "verdict" in problem for problem in problems), problems)

    def test_filename_selects_report_kind_and_unknown_names_fail(self):
        code, problems = self.run_report("review.md", main_report())
        self.assertEqual(code, 1)
        self.assertTrue(any("文件名只能是" in problem for problem in problems), problems)

        self.assert_rejected("review-razor.json", main_report(), "occams_razor")

    def test_invalid_json_and_missing_file_are_reported_without_tracebacks(self):
        code, problems = self.run_main(("group1", "round1", "review-main.json"), '{"verdict":')
        self.assertEqual(code, 1)
        self.assertTrue(any("JSON" in problem for problem in problems), problems)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["check.py", os.path.join(self.root, "group1", "round1", "missing.json")])
        self.assertEqual(code, 1)
        self.assertIn("无法读取", output.getvalue())

    def test_formal_report_location_and_round_are_hard_errors(self):
        cases = [
            (("group1", "round1"), "review-main-old.json", main_report(), "文件名只能是"),
            (("round1",), "review-razor.json", razor_report(), "group<G>/round<R>/"),
            (("group1", "round4"), "review-main.json", main_report(), "只能是 round1、round2、round3"),
            (("group01", "round1"), "review-main.json", main_report(), "group<G>"),
            (("group0", "round1"), "review-main.json", main_report(), "group<G>"),
        ]
        for folders, name, document, expected in cases:
            with self.subTest(path="/".join((*folders, name))):
                code, problems = self.run_report(name, document, folders)
                self.assertEqual(code, 1, problems)
                self.assertTrue(any(expected in problem for problem in problems), problems)

    def test_formal_diagnosis_location_is_a_hard_error(self):
        self.create_complete_formal_group(failed=True, group="group1")
        report = diagnosis_report()
        self.assertEqual(self.run_report("diagnosis.json", report, ("group1",)), (0, []))

        code, problems = self.run_report("diagnosis.json", report, ("group1", "round1"))
        self.assertEqual(code, 1)
        self.assertTrue(any("group<G>/diagnosis.json" in problem for problem in problems), problems)

    def test_formal_diagnosis_requires_round3(self):
        code, problems = self.run_report("diagnosis.json", diagnosis_report(), folders=("group1",))
        self.assertEqual(code, 1, problems)
        self.assertTrue(any("必须已有 round3" in problem for problem in problems), problems)

    def test_formal_diagnosis_rejects_third_round_when_all_reports_pass(self):
        self.create_complete_formal_group(failed=False, group="group1")
        code, problems = self.run_report("diagnosis.json", diagnosis_report(), folders=("group1",))
        self.assertEqual(code, 1, problems)
        self.assertTrue(any("至少要有一份结论为「不通过」" in problem for problem in problems), problems)

    def test_formal_diagnosis_rejects_an_invalid_required_report(self):
        self.create_complete_formal_group(failed=True, group="group1")
        report_path = Path(self.root) / "group1" / "round1" / "review-main.json"
        write_file(report_path.parent, report_path.name, "{invalid")

        code, problems = self.run_report(
            "diagnosis.json", diagnosis_report(), folders=("group1",),
        )

        self.assertEqual(code, 1, problems)
        self.assertTrue(
            any("review-main.json" in problem and "JSON 无效" in problem for problem in problems),
            problems,
        )

    def test_formal_diagnosis_requires_reports_submissions_and_handling_for_all_rounds(self):
        self.create_formal_round3(failed=True)
        code, problems = self.run_report("diagnosis.json", diagnosis_report(), folders=("group1",))
        self.assertEqual(code, 1, problems)
        self.assertTrue(any("round1" in problem for problem in problems), problems)
        advice = "上一轮有不通过时，下一轮主审、剃刀都要复审"
        for name in ("review-main.json", "review-razor.json"):
            self.assertTrue(
                any(f"缺少 {name}" in problem and advice in problem for problem in problems),
                problems,
            )

        group = Path(self.root) / "group1"
        for number in (1, 2):
            round_path = group / f"round{number}"
            write_file(round_path, "review-main.json", json.dumps(main_report(), ensure_ascii=False))
            write_file(round_path, "review-razor.json", json.dumps(razor_report(), ensure_ascii=False))
            write_file(round_path, "derivation-main.json", json.dumps(derivation_report(), ensure_ascii=False))
            write_file(round_path, "derivation-razor.json", json.dumps(derivation_report(), ensure_ascii=False))
        for number in (1, 2, 3):
            write_file(group / f"round{number}", "submitted-review.md", "")
        code, problems = self.run_report("diagnosis.json", diagnosis_report(), folders=("group1",))
        self.assertEqual(code, 1, problems)
        self.assertTrue(any("submitted" in problem for problem in problems), problems)

        for number in (1, 2, 3):
            write_file(group / f"round{number}", "submitted-review.md", "本轮审查版本。\n")
        code, problems = self.run_report("diagnosis.json", diagnosis_report(), folders=("group1",))
        self.assertEqual(code, 1, problems)
        self.assertTrue(any("handling.md" in problem for problem in problems), problems)

        write_file(group / "round3", "handling.md", "逐项处理了不通过意见。\n")
        self.assertEqual(self.run_report("diagnosis.json", diagnosis_report(), folders=("group1",)), (0, []))

    def test_formal_group_must_start_with_round1(self):
        code, problems = self.run_report("diagnosis.json", diagnosis_report(), ("group1",))
        self.assertEqual(code, 1)
        self.assertTrue(any("缺少 round1" in problem for problem in problems), problems)

    def test_formal_round_directories_are_continuous_and_limited_to_three(self):
        self.run_report("review-main.json", main_report(), ("group1", "round1"))
        code, problems = self.run_report("review-main.json", main_report(), ("group1", "round3"))
        self.assertEqual(code, 1)
        self.assertTrue(any("缺少 round2" in problem for problem in problems), problems)

        for invalid_round in ("round0", "round02", "round4"):
            with self.subTest(round=invalid_round):
                os.makedirs(os.path.join(self.root, "group1", invalid_round), exist_ok=True)
                code, problems = self.run_report("review-main.json", main_report(), ("group1", "round1"))
                self.assertEqual(code, 1)
                self.assertTrue(any(invalid_round in problem for problem in problems), problems)

    def test_opening_a_group_requires_a_valid_previous_diagnosis(self):
        folders = ("group2", "round1")
        code, problems = self.run_report("review-main.json", main_report(), folders)
        self.assertEqual(code, 1)
        self.assertTrue(any("一组要复审到第 3 轮" in problem for problem in problems), problems)

        self.create_complete_formal_group(failed=True, group="group1")
        write_file(self.root, "group1/diagnosis.json",
                   json.dumps(diagnosis_report(actions=["无效动作"])))
        code, problems = self.run_report("review-main.json", main_report(), folders)
        self.assertEqual(code, 1, problems)
        self.assertTrue(any("actions 只能包含" in problem for problem in problems), problems)

        write_file(self.root, "group1/diagnosis.json", json.dumps(diagnosis_report()))
        self.assertEqual(self.run_report("review-main.json", main_report(), folders), (0, []))

    def test_round3_failure_is_only_a_reminder(self):
        os.makedirs(os.path.join(self.root, "group1", "round1"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "group1", "round2"), exist_ok=True)
        document = main_report(verdict="不通过", hard_to_misuse=[{
            "problem": "缺陷",
            "evidence": "check.py:5",
            "quote": {"source": "xy-repo-rules 第 1 条", "text": "人类或 agent 读完 README.md 后都能立刻上手。"},
        }])
        code, output = self.run_report("review-main.json", document, ("group1", "round3"))
        self.assertEqual(code, 0, output)
        self.assertTrue(any("先派判因" in line for line in output), output)

    def test_exit_codes(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["check.py"]), 2)
