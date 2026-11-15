import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "xy-repo-map" / "tests"))
sys.path.insert(0, str(ROOT / "xy-cleanup"))

from map_fixture import feature, make_cleanup_repo  # noqa: E402
import check_reports as check_reports_module  # noqa: E402
from repo_map_lib.git_repository import tracked_paths  # noqa: E402
from repo_map_lib.metrics import count_non_whitespace_characters as character_count  # noqa: E402
from repo_map_lib.map_data import file_category_with_skip  # noqa: E402
from repo_map_lib.skip_paths import read_skip_paths  # noqa: E402


COMMAND = ROOT / "xy-cleanup" / "check_reports.py"
RULE_SOURCE = "xy-iron-law 第 3 条"
RULE_TEXT = "如无必要，勿增实体（奥卡姆剃刀）"
DUPLICATE_BLOCK_LINES = [
    "This shared paragraph explains the first concrete behavior clearly.",
    "This shared paragraph explains the second concrete behavior clearly.",
    "This shared paragraph explains the third concrete behavior clearly.",
    "This shared paragraph explains the fourth concrete behavior clearly.",
    "This shared paragraph explains the fifth concrete behavior clearly.",
    "This shared paragraph explains the sixth concrete behavior clearly.",
]


def razor_keep_item(feature_id, lines=None, source=None, text=None):
    default_lines = {
        "root": "README.md:1-1",
        "alpha": "alpha/main.py:1-1",
        "beta": "beta/main.py:1-1",
    }
    if source is None:
        source = f"{feature_id}:core"
    if text is None:
        source_feature, separator, item_id = source.partition(":")
        text = (
            f"提供 {source_feature} 功能。"
            if separator and item_id == "core" else {
                "要什么": f"提供 {feature_id} 功能。",
                "为什么": "让功能容易使用。",
                RULE_SOURCE: RULE_TEXT,
            }.get(source, "")
        )
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
        "rewrites": [],
        "usage_candidates": [],
        "keep": keep,
    }


def report(role, repository, feature_ids=("root", "alpha", "beta")):
    own_lines = {}
    data = None
    skip_paths = None
    if role == "剃刀":
        data = check_reports_module.build_map(repository)
        feature_data = data["features"]
        skip_paths = read_skip_paths(repository, tracked_paths(repository))
        own_lines = {
            feature_id: feature_data[feature_id]["lines"]["own"]
            for feature_id in feature_ids
        }
    reviews = []
    for feature_id in feature_ids:
        current = (
            review_feature(feature_id, role, own_lines[feature_id])
            if role == "剃刀" else review_feature(feature_id, role)
        )
        if role == "剃刀":
            direct_files = {
                path
                for group_paths in data["features"][feature_id]["files"].values()
                for path in group_paths
            }
            if feature_id == data["root"]:
                readme_lines = (Path(repository) / "README.md").read_bytes().splitlines()
                heading_lines = sorted(
                    check_reports_module._markdown_heading_lines(readme_lines) or ()
                )
                boundaries = sorted({1, *heading_lines})
                root_ranges = []
                for index, start in enumerate(boundaries):
                    end = (
                        boundaries[index + 1] - 1
                        if index + 1 < len(boundaries) else len(readme_lines)
                    )
                    if start <= end and any(
                        line.strip() for line in readme_lines[start - 1:end]
                    ):
                        root_ranges.append(f"README.md:{start}-{end}")
                current["keep"] = [
                    razor_keep_item("root", lines=line_range)
                    for line_range in root_ranges
                ]
            current["rewrites"] = [
                block
                for path in sorted(direct_files)
                if Path(path).name != "TODO.md"
                and file_category_with_skip(repository, path, skip_paths) == "docs"
                for block in rewrite_blocks(repository, path)
            ]
        reviews.append(current)
    return {"role": role, "repo": str(repository), "features": reviews}


def reports_for_feature(repository, feature_id):
    main = report("主审", repository)
    razor = report("剃刀", repository)
    selected = next(item for item in razor["features"] if item["feature"] == feature_id)
    return main, razor, selected


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


def rewrite_blocks(repository, path):
    content = (Path(repository) / path).read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    if not lines:
        return []
    return [{"lines": f"{path}:1-{len(lines)}", "text": content}]


class CheckReportsTest(unittest.TestCase):
    def setUp(self):
        self.repository = make_cleanup_repo(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.main_path = self.directory / "main.json"
        self.razor_path = self.directory / "razor.json"
        self.recheck_path = self.directory / "recheck.json"
        self.carry_over_path = self.directory / "previous-razor.json"
        self.usage_coverage_path = self.directory / "usage-coverage.json"
        self.test_candidates_path = self.directory / "test-candidates.json"
        self.test_candidates = {
            "runs": [],
            "unmeasured_files": [],
            "generated_mutants": 0,
            "mutants": [],
            "functions": {},
            "unmeasured_functions": [],
            "candidates": [],
        }
        self.write_test_candidates()
        self.recheck_paths = []
        self.add_tracked_file(
            "README.md",
            "Demo.\n\n## 实际使用\n\n本地场景说明。\n",
        )
        self.usage_coverage = {
            "sources": [{
                "source": "README.md:5-5",
                "scenarios": [],
                "why_none": "本地场景说明不能推出可运行的实际使用命令。",
            }],
            "files": {},
            "unmeasured": [
                {"path": "alpha/main.py", "why": "没有可安全运行的场景。"},
                {"path": "beta/main.py", "why": "没有可安全运行的场景。"},
            ],
        }
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))

    def write_reports(self, main, razor):
        data = check_reports_module.build_map(self.repository)
        skip_paths = read_skip_paths(self.repository, tracked_paths(self.repository))
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
            if isinstance(item.get("rewrites"), list):
                document_files = {
                    path for path in direct_files
                    if Path(path).name != "TODO.md"
                    and file_category_with_skip(self.repository, path, skip_paths)
                    == "docs"
                }
                existing_paths = {
                    parsed[0]
                    for rewrite in item["rewrites"]
                    if isinstance(rewrite, dict)
                    and (parsed := check_reports_module._parse_line_range(
                        rewrite.get("lines"), allow_single_line=False,
                    )) is not None
                }
                item["rewrites"].extend(
                    block
                    for path in sorted(document_files - existing_paths)
                    for block in rewrite_blocks(self.repository, path)
                )
            keep_items = item.get("keep")
            if isinstance(keep_items, list) and keep_items:
                required.append(recheck_report(
                    self.repository,
                    [feature_id],
                    keep_lines=[
                        item_keep["lines"]
                        for item_keep in keep_items
                        if isinstance(item_keep, dict)
                        and isinstance(item_keep.get("lines"), str)
                    ],
                ))
        if required:
            self.write_recheck_reports(required)
        else:
            self.clear_recheck_reports()
        self.main_path.write_text(json.dumps(main, ensure_ascii=False), encoding="utf-8")
        self.razor_path.write_text(json.dumps(razor, ensure_ascii=False), encoding="utf-8")
        self.usage_coverage_path.write_text(
            json.dumps(self.usage_coverage, ensure_ascii=False), encoding="utf-8",
        )

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

    def write_test_candidates(self):
        self.test_candidates_path.write_text(
            json.dumps(self.test_candidates, ensure_ascii=False), encoding="utf-8",
        )

    def add_pytest_measurement(self, source, listed, results, functions=None,
                               unmeasured_functions=None, candidates=None,
                               list_log="no tests ran in 0.02s\n", root="alpha"):
        test_path = f"{root}/test_api.py"
        self.add_tracked_file(test_path, source)
        self.add_tracked_file("alpha/main.py", "first = 1\nsecond = 2\n")
        artifacts = self.directory / "candidate-artifacts"
        artifacts.mkdir(exist_ok=True)

        def write_junit(name, cases):
            document = ET.Element("testsuites")
            suite = ET.SubElement(document, "testsuite")
            for case in cases:
                testcase = ET.SubElement(
                    suite, "testcase",
                    {key: str(case[key]) for key in ("classname", "name", "file", "line")
                     if key in case},
                )
                for child in case.get("children", []):
                    ET.SubElement(testcase, child)
            path = artifacts / name
            ET.ElementTree(document).write(path, encoding="utf-8", xml_declaration=True)
            return path

        list_path = write_junit("pytest-list.xml", listed)
        junit_path = write_junit("pytest-run.xml", results)
        log_path = artifacts / "pytest-list.log"
        log_path.write_text(list_log, encoding="utf-8")
        self.test_candidates = {
            "runs": [{
                "runner": "pytest", "root": root, "command": "pytest -q",
                "junit": str(junit_path.relative_to(self.directory)),
                "list": str(list_path.relative_to(self.directory)),
                "list_log": str(log_path.relative_to(self.directory)),
            }],
            "unmeasured_files": [],
            "generated_mutants": 2,
            "mutants": [
                {"id": "mutant-1", "location": "alpha/main.py:1", "change": "first = 0"},
                {"id": "mutant-2", "location": "alpha/main.py:2", "change": "second = 0"},
            ],
            "functions": {} if functions is None else functions,
            "unmeasured_functions": (
                [] if unmeasured_functions is None else unmeasured_functions
            ),
            "candidates": [] if candidates is None else candidates,
        }
        self.write_test_candidates()
        main, razor = report("主审", self.repository), report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"][0]["lines"] = "alpha/main.py:1-2"
        definitions = {
            line for line, content in enumerate(source.splitlines(), start=1)
            if content.lstrip().startswith(("def ", "async def "))
        }
        source_lines = source.splitlines()
        for line in sorted(definitions):
            start = line
            while start > 1 and source_lines[start - 2].lstrip().startswith("@"):
                start -= 1
            end = line
            while end < len(source_lines) and source_lines[end].startswith((" ", "\t")):
                end += 1
            alpha["keep"].append(razor_keep_item("alpha", f"{test_path}:{start}-{end}"))
        for candidate in self.test_candidates["candidates"]:
            candidate_function = self.test_candidates["functions"].get(candidate["function"], {})
            for keep in alpha["keep"]:
                parsed = check_reports_module._parse_line_range(keep.get("lines"), False)
                if (parsed is None or parsed[0] != candidate_function.get("file")
                        or parsed[1] != candidate_function.get("line")):
                    continue
                for witness_id in candidate.get("covered_by", []):
                    witness = self.test_candidates["functions"].get(witness_id, {})
                    if witness.get("file") and witness.get("line"):
                        keep["cost"] += (
                            f" 断言检查的行为仍需保留，覆盖证据见 {witness['file']}:{witness['line']}。"
                        )
        self.write_reports(main, razor)
        return test_path

    def add_nextest_measurement(self, source, list_document, result_cases,
                                functions=None, unmeasured_functions=None,
                                candidates=None, test_path="alpha/tests.rs"):
        self.add_tracked_file(test_path, source)
        self.add_tracked_file("alpha/main.py", "first = 1\nsecond = 2\n")
        artifacts = self.directory / "candidate-artifacts"
        artifacts.mkdir(exist_ok=True)
        list_path = artifacts / "nextest-list.json"
        list_path.write_text(json.dumps(list_document), encoding="utf-8")
        junit_path = artifacts / "nextest-run.xml"
        document = ET.Element("testsuites")
        for suite_name, cases in result_cases.items():
            suite = ET.SubElement(document, "testsuite", {"name": suite_name})
            for case in cases:
                testcase = ET.SubElement(suite, "testcase", {"name": case["name"]})
                for child in case.get("children", []):
                    ET.SubElement(testcase, child)
        ET.ElementTree(document).write(junit_path, encoding="utf-8", xml_declaration=True)
        self.test_candidates = {
            "runs": [{
                "runner": "nextest", "root": "alpha", "command": "cargo nextest run",
                "junit": str(junit_path.relative_to(self.directory)),
                "list": str(list_path.relative_to(self.directory)),
            }],
            "unmeasured_files": [],
            "generated_mutants": 2,
            "mutants": [
                {"id": "mutant-1", "location": "alpha/main.py:1", "change": "first = 0"},
                {"id": "mutant-2", "location": "alpha/main.py:2", "change": "second = 0"},
            ],
            "functions": {} if functions is None else functions,
            "unmeasured_functions": (
                [] if unmeasured_functions is None else unmeasured_functions
            ),
            "candidates": [] if candidates is None else candidates,
        }
        self.write_test_candidates()
        main, razor = report("主审", self.repository), report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"][0]["lines"] = "alpha/main.py:1-2"
        lines = source.splitlines()
        for number, content in enumerate(lines, start=1):
            if content.startswith("fn ") or content.startswith("async fn "):
                end = number
                while end < len(lines) and (
                    lines[end].startswith((" ", "\t")) or lines[end].strip() == "}"
                ):
                    end += 1
                start = number - 1
                while start > 0 and (lines[start - 1].lstrip().startswith(("#[", "///"))):
                    start -= 1
                alpha["keep"].append(
                    razor_keep_item("alpha", f"{test_path}:{start + 1}-{end}"),
                )
        for candidate in self.test_candidates["candidates"]:
            candidate_function = self.test_candidates["functions"].get(candidate["function"], {})
            for keep in alpha["keep"]:
                parsed = check_reports_module._parse_line_range(keep.get("lines"), False)
                if (parsed is None or parsed[0] != candidate_function.get("file")
                        or parsed[1] > candidate_function.get("line", 0)
                        or parsed[2] < candidate_function.get("line", 0)):
                    continue
                for witness_id in candidate.get("covered_by", []):
                    witness = self.test_candidates["functions"].get(witness_id, {})
                    if witness.get("file") and witness.get("line"):
                        keep["cost"] += (
                            f" 断言检查的行为仍需保留，覆盖证据见 {witness['file']}:{witness['line']}。"
                        )
        self.write_reports(main, razor)
        return test_path

    def assert_candidate_error(self, expected):
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(expected, result.stdout + result.stderr)

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

    def add_tracked_file(self, relative_path, content):
        path = self.repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", relative_path], check=True)
        if (hasattr(self, "usage_coverage")
                and file_category_with_skip(self.repository, relative_path, []) == "code"
                and all(item.get("path") != relative_path
                        for item in self.usage_coverage["unmeasured"])):
            self.usage_coverage["unmeasured"].append({
                "path": relative_path, "why": "没有可安全运行的场景。",
            })

    def check_guide_rewrites(self, original, rewrites):
        self.add_tracked_file("alpha/guide.md", original)
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["rewrites"] = rewrites
        deleted = all(block["text"] == "" for block in rewrites)
        if not deleted:
            alpha["keep"].append(
                razor_keep_item("alpha", f"alpha/guide.md:1-{len(original.splitlines())}"),
            )
        original_chars = character_count(original)
        rewritten_chars = sum(character_count(block["text"]) for block in rewrites)
        if rewritten_chars < original_chars:
            rewrite_finding = finding("删除" if deleted else "重构")
            rewrite_finding["target"] = f"alpha/guide.md:1-{len(original.splitlines())}"
            alpha["findings"].append(rewrite_finding)
        self.write_reports(main, razor)
        return self.run_check()

    def check_duplicate_documents(self, first_lines, second_lines,
                                  first_cost=None, second_cost=None):
        self.add_tracked_file("alpha/first.md", "\n".join(first_lines) + "\n")
        self.add_tracked_file("alpha/second.md", "\n".join(second_lines) + "\n")
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
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

    def run_check(self, scope=("全部",), reports=None, review="全部",
                  usage_coverage=True, carry_over=("无",), with_test_candidates=True):
        paths = self.reports_for_check() if reports is None else reports
        arguments = ["python3", "-B", str(COMMAND), "--repo", str(self.repository),
                     "--scope", *scope]
        if review is not None:
            arguments.extend(("--review", review))
        if carry_over is not None:
            arguments.extend(("--carry-over", *carry_over))
        if with_test_candidates:
            arguments.extend(("--test-candidates", str(self.test_candidates_path)))
        if usage_coverage:
            coverage_path = self.usage_coverage_path
            if (scope != ("全部",) or review != "全部") and review in {"变过的", "全部"}:
                data = check_reports_module.build_map(self.repository)
                try:
                    check_reports_module.validate_scope(data, list(scope), "--scope")
                except check_reports_module.ArgumentError:
                    pass
                else:
                    records = check_reports_module.read_approvals(
                        self.repository,
                        self.repository / check_reports_module.APPROVED_RELATIVE_PATH,
                    )
                    values, _ = check_reports_module.feature_values(self.repository)
                    fingerprints = check_reports_module.feature_fingerprints(
                        self.repository, data, values,
                    )
                    expected = {
                        feature_id
                        for feature_id, _reason in check_reports_module.review_features(
                            data, fingerprints, records, list(scope), review,
                        )
                    }
                    direct_code_files = set()
                    skip_paths = read_skip_paths(self.repository, tracked_paths(self.repository))
                    for feature_id in expected:
                        feature_data = data["features"][feature_id]
                        direct_code_files.update(
                            path
                            for paths_for_group in feature_data["files"].values()
                            for path in paths_for_group
                            if file_category_with_skip(
                                self.repository, path, skip_paths,
                            ) == "code"
                        )
                    coverage = json.loads(json.dumps(self.usage_coverage))
                    coverage["files"] = {
                        path: value for path, value in coverage["files"].items()
                        if path in direct_code_files
                    }
                    coverage["unmeasured"] = [
                        item for item in coverage["unmeasured"]
                        if item["path"] in direct_code_files
                    ]
                    coverage_path = self.directory / "scoped-usage-coverage.json"
                    coverage_path.write_text(
                        json.dumps(coverage, ensure_ascii=False), encoding="utf-8",
                    )
            arguments.extend(("--usage-coverage", str(coverage_path)))
        if with_test_candidates:
            arguments.extend(("--test-candidates", str(self.test_candidates_path)))
        arguments.extend(("--reports", *paths))
        return subprocess.run(
            arguments,
            capture_output=True, text=True, check=False,
        )

    def run_check_and_assert(self, message, scope=("全部",), reports=None, review="全部",
                             carry_over=("无",)):
        result = self.run_check(
            scope=scope, reports=reports, review=review, carry_over=carry_over,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(message, result.stdout)
        return result

    def test_accepts_complete_reports_for_each_role(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_carry_over_argument_is_required(self):
        result = self.run_check(carry_over=None)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--carry-over", result.stderr)

    def prepare_carried_finding(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        alpha_main = main["features"][1]
        alpha_razor = razor["features"][1]
        alpha_razor["keep"] = []
        razor_finding = finding("删除", False, "直接做")
        razor_finding["target"] = "alpha"
        alpha_razor["findings"] = [razor_finding]
        current_finding = finding("重构", True, "问你")
        current_finding["target"] = "alpha/main.py:1"
        alpha_main["findings"] = [current_finding]
        self.write_reports(main, razor)

        previous = report("剃刀", self.repository, ("alpha",))
        previous["features"][0]["findings"] = [finding()]
        self.carry_over_path.write_text(
            json.dumps(previous, ensure_ascii=False), encoding="utf-8",
        )
        carried = {
            "old": f"{self.carry_over_path}#alpha#1",
            "verdict": "仍成立",
            "target": "alpha/main.py:1",
            "evidence": "核对了 README.md:5 的实际使用记录。",
        }
        review = recheck_report(self.repository, ["alpha"], keep_lines=[])
        recheck_finding = finding("重构", True, "问你")
        recheck_finding["target"] = "alpha/main.py:1"
        review["features"][0]["findings"] = [recheck_finding]
        review["features"][0]["carried"] = [carried]
        self.write_recheck_reports([*self.read_recheck_reports(), review])
        return carried

    def test_carried_finding_adds_its_feature_to_required_rechecks(self):
        self.prepare_carried_finding()

        result = self.run_check(carry_over=(str(self.carry_over_path),))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_carry_over_uses_the_explicit_scope_even_when_plan_skips_a_feature(self):
        main = report("主审", self.repository, ())
        razor = report("剃刀", self.repository, ())
        self.write_reports(main, razor)
        data = check_reports_module.build_map(self.repository)
        values, _ = check_reports_module.feature_values(self.repository)
        fingerprints = check_reports_module.feature_fingerprints(
            self.repository, data, values,
        )
        (self.repository / "repo-map" / "approved.json").write_text(
            json.dumps({"alpha": fingerprints["alpha"]}), encoding="utf-8",
        )

        previous = report("剃刀", self.repository, ("alpha",))
        previous["features"][0]["findings"] = [finding()]
        self.carry_over_path.write_text(
            json.dumps(previous, ensure_ascii=False), encoding="utf-8",
        )
        carried = {
            "old": f"{self.carry_over_path}#alpha#1",
            "verdict": "仍成立",
            "target": "alpha/main.py:1",
            "evidence": "核对了 README.md:5 的实际使用记录。",
        }
        recheck = recheck_report(self.repository, ["alpha"], keep_lines=[])
        recheck["features"][0]["findings"] = [finding()]
        recheck["features"][0]["carried"] = [carried]
        self.write_recheck_reports([recheck])

        result = self.run_check(
            scope=("alpha",), review="变过的",
            carry_over=(str(self.carry_over_path),),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_carry_over_rejects_missing_previous_findings(self):
        self.prepare_carried_finding()
        review = self.read_recheck_for_feature("alpha")
        review["features"][0]["carried"] = []
        self.write_recheck_reports([*self.read_recheck_reports()[:-1], review])

        self.run_check_and_assert(
            "carried 缺少上一轮发现", carry_over=(str(self.carry_over_path),),
        )

    def test_carry_over_rejects_duplicate_previous_findings(self):
        carried = self.prepare_carried_finding()
        review = self.read_recheck_for_feature("alpha")
        review["features"][0]["carried"].append(dict(carried))
        self.write_recheck_reports([*self.read_recheck_reports()[:-1], review])

        self.run_check_and_assert(
            "carried 重复上一轮发现", carry_over=(str(self.carry_over_path),),
        )

    def test_carry_over_rejects_an_old_reference_that_does_not_match(self):
        carried = self.prepare_carried_finding()
        review = self.read_recheck_for_feature("alpha")
        review["features"][0]["carried"][0]["old"] = (
            f"{self.carry_over_path}#alpha#2"
        )
        self.write_recheck_reports([*self.read_recheck_reports()[:-1], review])

        self.run_check_and_assert(
            "对不上上一轮发现", carry_over=(str(self.carry_over_path),),
        )

    def test_still_valid_carried_finding_needs_a_matching_current_finding(self):
        carried = self.prepare_carried_finding()
        carried["target"] = "beta/main.py:1"
        review = self.read_recheck_for_feature("alpha")
        review["features"][0]["carried"] = [carried]
        self.write_recheck_reports([*self.read_recheck_reports()[:-1], review])

        self.run_check_and_assert(
            "carried[0].target 必须对应同一份复审报告中的发现",
            carry_over=(str(self.carry_over_path),),
        )

    def test_no_carry_over_rejects_carried_fields(self):
        review = self.read_recheck_for_feature("alpha")
        review["features"][0]["carried"] = []
        self.write_recheck_reports([*self.read_recheck_reports()[:-1], review])

        self.run_check_and_assert("有多余字段 carried")
    def test_test_candidates_argument_is_required(self):
        result = self.run_check(with_test_candidates=False)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--test-candidates", result.stderr)

    def test_test_candidates_file_and_top_level_shape_are_required(self):
        self.test_candidates_path.unlink()
        result = self.run_check()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--test-candidates", result.stderr)

        self.test_candidates = []
        self.write_test_candidates()
        self.assert_candidate_error("test-candidates必须是对象")

        self.test_candidates = {
            "runs": [], "unmeasured_files": [], "generated_mutants": 0,
            "mutants": [], "functions": {}, "unmeasured_functions": [],
            "candidates": [], "extra": True,
        }
        self.write_test_candidates()
        self.assert_candidate_error("有多余字段 extra")

    def test_empty_runs_require_each_direct_test_file_to_be_unmeasured(self):
        self.add_tracked_file("alpha/test_api.py", "def test_one():\n    assert True\n")
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"] = [
            razor_keep_item("alpha", "alpha/main.py:1-1"),
            razor_keep_item("alpha", "alpha/test_api.py:1-2"),
        ]
        self.write_reports(main, razor)
        self.assert_candidate_error("test_api.py")

        self.test_candidates["unmeasured_files"] = [{
            "path": "alpha/test_api.py", "why": "副本没有该测试运行器。",
        }]
        self.write_test_candidates()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_runs_can_cover_multiple_roots_with_the_same_runner(self):
        self.add_pytest_measurement("", [], [])
        self.test_candidates["unmeasured_files"] = [{
            "path": "alpha/test_api.py", "why": "副本没有该测试运行器。",
        }]
        second_root = self.repository / "alpha" / "alternate-tests"
        second_root.mkdir(parents=True)
        artifacts = self.directory / "candidate-artifacts"
        list_path = artifacts / "pytest-list-second.xml"
        junit_path = artifacts / "pytest-run-second.xml"
        for path in (list_path, junit_path):
            ET.ElementTree(ET.Element("testsuites")).write(
                path, encoding="utf-8", xml_declaration=True,
            )
        log_path = artifacts / "pytest-list-second.log"
        log_path.write_text("no tests ran in 0.01s\n", encoding="utf-8")
        self.test_candidates["runs"].append({
            "runner": "pytest", "root": "alpha/alternate-tests",
            "command": "pytest -q", "junit": str(junit_path.relative_to(self.directory)),
            "list": str(list_path.relative_to(self.directory)),
            "list_log": str(log_path.relative_to(self.directory)),
        })
        self.write_test_candidates()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pytest_list_log_and_junit_fields_are_validated(self):
        rows = [{"classname": "test_api", "name": "test_one", "file": "test_api.py", "line": "0"}]
        source = "def test_one():\n    assert True\n"
        for log, message in (
            ("1 deselected in 0.02s\n", "deselected"),
            ("error collecting tests\n", "error"),
            ("1 passed in 0.02s\n", "no tests ran"),
        ):
            with self.subTest(log=log):
                self.add_pytest_measurement(source, rows, rows, list_log=log)
                self.assert_candidate_error(message)

        self.add_pytest_measurement(source, rows, [{**rows[0], "line": "1"}])
        self.assert_candidate_error("junit")

    def test_nextest_list_rejects_filtered_binary_and_expression_but_keeps_ignored(self):
        source = "#[test]\nfn test_one() {\n    assert!(true);\n}\n"
        ignored = {
            "rust-suites": {"api": {
                "status": "listed",
                "testcases": {
                    "test_one": {"ignored": True,
                                 "filter-match": {"status": "filtered-out", "reason": "ignored"}},
                },
            }},
        }
        self.add_nextest_measurement(source, ignored, {})
        self.test_candidates["unmeasured_functions"] = [{
            "id": "alpha::api::test_one", "file": "alpha/tests.rs", "why": "被 ignore 跳过。",
        }]
        self.write_test_candidates()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.test_candidates["unmeasured_functions"] = []
        self.test_candidates["functions"] = {"alpha::api::test_one": {
            "file": "alpha/tests.rs", "line": 2,
            "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
        }}
        self.write_test_candidates()
        self.assert_candidate_error("必须量不了")

        for suites, expected in (
            ({"api": {"status": "filtered", "testcases": {}}}, "status"),
            ({"api": {"status": "listed", "testcases": {
                "test_one": {"ignored": False,
                             "filter-match": {"status": "filtered-out", "reason": "expression"}},
            }}}, "expression"),
        ):
            with self.subTest(expected=expected):
                listing = {"rust-suites": suites}
                self.add_nextest_measurement(source, listing, {})
                self.test_candidates["unmeasured_functions"] = [{
                    "id": "alpha::api::test_one", "file": "alpha/tests.rs", "why": "没运行。",
                }]
                self.write_test_candidates()
                self.assert_candidate_error(expected)

    def test_runner_list_fields_are_exact(self):
        rows = [{"classname": "test_api", "name": "test_one", "file": "test_api.py", "line": "0"}]
        self.add_pytest_measurement("def test_one():\n    assert True\n", rows, rows)
        del self.test_candidates["runs"][0]["list"]
        self.write_test_candidates()
        self.assert_candidate_error("list")

        nextest_list = {"rust-suites": {"api": {"status": "listed", "testcases": {}}}}
        self.add_nextest_measurement("", nextest_list, {})
        self.test_candidates["runs"][0]["list_log"] = "bad"
        self.write_test_candidates()
        self.assert_candidate_error("list_log")

    def test_test_items_and_function_inventory_must_match_the_collection(self):
        rows = [{"classname": "test_api", "name": "test_one", "file": "test_api.py", "line": "0"}]
        source = "def test_one():\n    assert True\n"
        self.add_pytest_measurement(source, rows, [{**rows[0], "classname": "different"}])
        self.assert_candidate_error("全集")

        for listing, results, expected in (
            (rows + rows, rows, "重复"),
            (rows, rows + rows, "重复"),
        ):
            with self.subTest(expected=expected, result_count=len(results)):
                self.add_pytest_measurement(source, listing, results)
                self.assert_candidate_error("重复")

        self.add_pytest_measurement(source, rows, rows)
        self.test_candidates["unmeasured_functions"] = [{
            "id": "extra", "file": "alpha/test_api.py", "why": "多余。",
        }]
        self.write_test_candidates()
        self.assert_candidate_error("函数")

    def test_pytest_testcase_requires_file_line_and_existing_root_file(self):
        row = {"classname": "test_api", "name": "test_one", "line": "0"}
        self.add_pytest_measurement(
            "def test_one():\n    assert True\n", [row], [row],
        )
        self.assert_candidate_error("file")

        row = {"classname": "test_api", "name": "test_one", "file": "missing.py", "line": "0"}
        self.add_pytest_measurement("def test_one():\n    assert True\n", [row], [row])
        self.assert_candidate_error("不存在")

    def test_parameterized_and_inherited_items_share_one_function_and_missing_one_excludes_it(self):
        source = "def test_case(value):\n    assert value\n"
        listed = [
            {"classname": "TestFirst", "name": "test_case[one]", "file": "test_api.py", "line": "0"},
            {"classname": "TestFirst", "name": "test_case[two]", "file": "test_api.py", "line": "0"},
            {"classname": "TestSecond", "name": "test_case[one]", "file": "test_api.py", "line": "0"},
        ]
        self.add_pytest_measurement(source, listed, listed[:-1])
        self.test_candidates["unmeasured_functions"] = [{
            "id": "alpha/test_api.py:1::test_case", "file": "alpha/test_api.py",
            "why": "一个继承用例没运行。",
        }]
        self.write_test_candidates()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        function_id = "alpha/test_api.py:1::test_case"
        self.test_candidates["functions"][function_id] = {
            "file": "alpha/test_api.py", "line": 1,
            "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
        }
        self.test_candidates["unmeasured_functions"] = []
        self.write_test_candidates()
        self.assert_candidate_error("量不了")

    def test_pytest_function_location_must_match_the_discovered_declaration(self):
        row = {"classname": "test_api", "name": "test_one", "file": "test_api.py", "line": "0"}
        self.add_pytest_measurement(
            "@staticmethod\ndef test_one():\n    assert True\n", [row], [row],
            functions={"alpha/test_api.py:1::test_one": {
                "file": "alpha/test_api.py", "line": 2,
                "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
            }},
        )
        self.assert_candidate_error("line")

        self.add_pytest_measurement(
            "@staticmethod\ndef test_one():\n    assert True\n", [row], [row],
            functions={"alpha/test_api.py:1::test_one": {
                "file": "alpha/test_api.py", "line": 1,
                "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
            }},
        )
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_nextest_functions_need_matching_fn_and_test_attribute(self):
        listing = {"rust-suites": {"api": {"status": "listed", "testcases": {
            "tests::test_one": {"ignored": False, "filter-match": {"status": "matches"}},
        }}}}
        for source, message in (
            ("#[tokio::test]\nasync fn test_one() {\n}\n", None),
            ("fn not_test_one() {\n}\n", "fn"),
            ("fn test_one() {\n}\n", "Rust 测试属性"),
        ):
            with self.subTest(message=message):
                function_line = 2 if source.startswith("#[tokio::test]") else 1
                self.add_nextest_measurement(
                    source, listing, {"api": [{"name": "tests::test_one"}]},
                    functions={"alpha::api::tests::test_one": {
                        "file": "alpha/tests.rs", "line": function_line,
                        "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
                    }},
                )
                if message is None:
                    result = self.run_check()
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assert_candidate_error(message)

    def test_candidate_ranges_include_decorators_and_rust_attributes(self):
        row = {"classname": "test_api", "name": "test_one", "file": "test_api.py", "line": "0"}
        self.add_pytest_measurement(
            "@staticmethod\ndef test_one():\n    assert True\n", [row], [row],
            functions={"alpha/test_api.py:1::test_one": {
                "file": "alpha/test_api.py", "line": 1,
                "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
            }},
        )
        self.test_candidates["unmeasured_files"] = []
        self.test_candidates["functions"]["alpha/test_api.py:1::test_one"]["covers"] = []
        self.write_test_candidates()
        self.assert_candidate_error("covers")

        subprocess.run(
            ["git", "-C", str(self.repository), "rm", "-q", "-f", "alpha/test_api.py"],
            check=True,
        )
        rust_source = "/// docs\n#[test]\nfn test_one() {\n}\n"
        listing = {"rust-suites": {"api": {"status": "listed", "testcases": {
            "test_one": {"ignored": False, "filter-match": {"status": "matches"}},
        }}}}
        self.add_nextest_measurement(rust_source, listing, {"api": [{"name": "test_one"}]},
                                     functions={"alpha::api::test_one": {
                                         "file": "alpha/tests.rs", "line": 3,
                                         "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
                                     }})
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.add_nextest_measurement(
            "#[test]\nfn test_one() {\n}\nnot_formatted();\n", listing,
            {"api": [{"name": "test_one"}]},
            functions={"alpha::api::test_one": {
                "file": "alpha/tests.rs", "line": 2,
                "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
            }},
            candidates=[{"function": "alpha::api::test_one", "covered_by": ["x"]}],
        )
        self.assert_candidate_error("candidates")

    def test_only_incomplete_functions_are_unmeasured(self):
        source = "def test_first():\n    assert True\n\ndef test_second():\n    assert True\n"
        first = {"classname": "test_api", "name": "test_first", "file": "test_api.py", "line": "0"}
        second = {"classname": "test_api", "name": "test_second", "file": "test_api.py", "line": "3"}
        self.add_pytest_measurement(source, [first, second], [first, {**second, "children": ["skipped"]}],
                                    functions={"alpha/test_api.py:1::test_first": {
                                        "file": "alpha/test_api.py", "line": 1,
                                        "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
                                    }})
        self.test_candidates["unmeasured_functions"] = [{
            "id": "alpha/test_api.py:4::test_second", "file": "alpha/test_api.py",
            "why": "运行结果 skipped。",
        }]
        self.write_test_candidates()
        self.assertEqual(self.run_check().returncode, 0)

        self.test_candidates["functions"]["alpha/test_api.py:4::test_second"] = {
            "file": "alpha/test_api.py", "line": 4,
            "covers": ["alpha/main.py:2-2"], "kills": ["mutant-2"],
        }
        self.test_candidates["unmeasured_functions"] = []
        self.write_test_candidates()
        self.assert_candidate_error("量不了")

    def test_direct_test_files_need_function_or_unmeasured_file_record(self):
        source = "def test_one():\n    assert True\n"
        row = {"classname": "test_api", "name": "test_one", "file": "test_api.py", "line": "0"}
        self.add_pytest_measurement(source, [row], [row])
        self.test_candidates["unmeasured_files"] = [{"path": "alpha/test_api.py", "why": "没测。"}]
        self.write_test_candidates()
        self.assert_candidate_error("同时")

        self.test_candidates["unmeasured_files"] = []
        self.test_candidates["functions"] = {}
        self.test_candidates["unmeasured_functions"] = []
        self.write_test_candidates()
        self.assert_candidate_error("test_api.py")

    def test_measured_functions_need_nonempty_coverage_and_known_mutants(self):
        row = {"classname": "test_api", "name": "test_one", "file": "test_api.py", "line": "0"}
        source = "def test_one():\n    assert True\n"
        function = {"file": "alpha/test_api.py", "line": 1,
                    "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"]}
        for mutation, expected in (
            ({"covers": []}, "covers"),
            ({"kills": ["missing"]}, "mutants"),
        ):
            with self.subTest(expected=expected):
                values = {**function, **mutation}
                self.add_pytest_measurement(
                    source, [row], [row], functions={"alpha/test_api.py:1::test_one": values},
                )
                self.assert_candidate_error(expected)

        self.add_pytest_measurement(source, [row], [row],
                                    functions={"alpha/test_api.py:1::test_one": function})
        self.test_candidates["mutants"] = []
        self.test_candidates["generated_mutants"] = 0
        self.write_test_candidates()
        self.assert_candidate_error("mutants 不能为空")

    def test_candidates_are_recomputed_with_noncandidate_witnesses(self):
        source = "def test_small():\n    assert True\n\ndef test_large():\n    assert True\n"
        rows = [
            {"classname": "test_api", "name": "test_small", "file": "test_api.py", "line": "0"},
            {"classname": "test_api", "name": "test_large", "file": "test_api.py", "line": "3"},
        ]
        small = "alpha/test_api.py:1::test_small"
        large = "alpha/test_api.py:4::test_large"
        functions = {
            small: {"file": "alpha/test_api.py", "line": 1,
                    "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"]},
            large: {"file": "alpha/test_api.py", "line": 4,
                    "covers": ["alpha/main.py:1-2"], "kills": ["mutant-1", "mutant-2"]},
        }
        self.add_pytest_measurement(source, rows, rows, functions=functions,
                                    candidates=[{"function": small, "covered_by": [large]}])
        self.assertEqual(self.run_check().returncode, 0)

        equal_functions = {
            small: {"file": "alpha/test_api.py", "line": 1,
                    "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"]},
            large: {"file": "alpha/test_api.py", "line": 4,
                    "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"]},
        }
        self.add_pytest_measurement(
            source, rows, rows, functions=equal_functions,
            candidates=[{"function": small, "covered_by": [large]}],
        )
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        for candidates, expected in (
            ([{"function": large, "covered_by": [small]}], "candidates"),
            ([], "candidates"),
            ([{"function": small, "covered_by": []}], "covered_by"),
            ([{"function": small, "covered_by": [small]}], "候选"),
        ):
            with self.subTest(candidates=candidates):
                self.test_candidates["candidates"] = candidates
                self.write_test_candidates()
                self.assert_candidate_error(expected)

    def test_razor_must_cover_candidate_as_whole_and_name_a_witness(self):
        source = "def test_small():\n    assert True\n\ndef test_large():\n    assert True\n"
        rows = [
            {"classname": "test_api", "name": "test_small", "file": "test_api.py", "line": "0"},
            {"classname": "test_api", "name": "test_large", "file": "test_api.py", "line": "3"},
        ]
        small = "alpha/test_api.py:1::test_small"
        large = "alpha/test_api.py:4::test_large"
        functions = {
            small: {"file": "alpha/test_api.py", "line": 1,
                    "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"]},
            large: {"file": "alpha/test_api.py", "line": 4,
                    "covers": ["alpha/main.py:1-2"], "kills": ["mutant-1", "mutant-2"]},
        }
        self.add_pytest_measurement(source, rows, rows, functions=functions,
                                    candidates=[{"function": small, "covered_by": [large]}])
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"] = [
            razor_keep_item("alpha", "alpha/main.py:1-2"),
            razor_keep_item("alpha", "alpha/test_api.py:1-2"),
            razor_keep_item("alpha", "alpha/test_api.py:4-5"),
        ]
        self.write_reports(main, razor)
        self.assert_candidate_error("keep.cost")

        alpha["keep"][1]["cost"] += " 覆盖它的测试在 alpha/test_api.py:4。"
        self.write_reports(main, razor)
        self.assertEqual(self.run_check().returncode, 0)

        alpha["findings"] = [finding("删除")]
        alpha["findings"][0]["target"] = "alpha/test_api.py:1"
        self.write_reports(main, razor)
        self.assert_candidate_error("完整范围")

        alpha["keep"] = [
            razor_keep_item("alpha", "alpha/main.py:1-2"),
            razor_keep_item("alpha", "alpha/test_api.py:1-1"),
            razor_keep_item("alpha", "alpha/test_api.py:4-5"),
        ]
        alpha["findings"] = [finding("重构")]
        alpha["findings"][0]["target"] = "alpha/test_api.py:2-2"
        self.write_reports(main, razor)
        self.assert_candidate_error("完整范围")

        alpha["keep"] = [
            razor_keep_item("alpha", "alpha/main.py:1-2"),
            razor_keep_item("alpha", "alpha/test_api.py:4-5"),
        ]
        alpha["findings"] = [finding("删除")]
        alpha["findings"][0]["target"] = "alpha/test_api.py:1-2"
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        alpha["keep"] = [razor_keep_item("alpha", "alpha/main.py:1-2")]
        alpha["findings"] = [finding("重构")]
        alpha["findings"][0]["target"] = "alpha/test_api.py:1-2"
        self.write_reports(main, razor)
        self.assert_candidate_error("候选")

    def test_missing_runner_artifacts_and_unexpected_nextest_fields_are_rejected(self):
        row = {"classname": "test_api", "name": "test_one", "file": "test_api.py", "line": "0"}
        self.add_pytest_measurement("def test_one():\n    assert True\n", [row], [row])
        del self.test_candidates["runs"][0]["list_log"]
        self.write_test_candidates()
        self.assert_candidate_error("list_log")

        self.add_nextest_measurement("", {"rust-suites": {}}, {})
        del self.test_candidates["runs"][0]["list"]
        self.write_test_candidates()
        self.assert_candidate_error("list")

        self.add_nextest_measurement("", {"rust-suites": {}}, {})
        self.test_candidates["runs"][0]["list_log"] = "unexpected"
        self.write_test_candidates()
        self.assert_candidate_error("list_log")

    def test_pytest_run_must_match_each_list_attribute_and_function_set(self):
        row = {"classname": "test_api", "name": "test_one", "file": "test_api.py", "line": "0"}
        source = "def test_one():\n    assert True\n"
        for field, value in (("classname", "other"), ("name", "other"),
                             ("file", "main.py"), ("line", "1")):
            with self.subTest(field=field):
                self.add_pytest_measurement(source, [row], [{**row, field: value}])
                self.assert_candidate_error("全集")

        for functions, unmeasured, expected in (
            ({}, [], "函数"),
            ({}, [{"id": "extra", "file": "alpha/test_api.py", "why": "额外。"}], "函数"),
            ({"alpha/test_api.py:1::test_one": {
                "file": "alpha/test_api.py", "line": 1,
                "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
            }}, [{"id": "alpha/test_api.py:1::test_one", "file": "alpha/test_api.py",
                  "why": "重复。"}], "重叠"),
        ):
            with self.subTest(expected=expected, functions=list(functions)):
                self.add_pytest_measurement(source, [row], [row], functions=functions,
                                            unmeasured_functions=unmeasured)
                self.assert_candidate_error(expected)

        row_without_line = {key: value for key, value in row.items() if key != "line"}
        self.add_pytest_measurement(source, [row_without_line], [row_without_line])
        self.assert_candidate_error("line")

    def test_pytest_unittest_method_and_decorated_range_start_at_first_decorator(self):
        source = (
            "import unittest\n"
            "class Checks(unittest.TestCase):\n"
            "    @staticmethod\n"
            "    def test_one():\n"
            "        assert True\n"
        )
        row = {"classname": "Checks", "name": "test_one", "file": "test_api.py", "line": "2"}
        function = {"file": "alpha/test_api.py", "line": 3,
                    "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"]}
        self.add_pytest_measurement(
            source, [row], [row], functions={"alpha/test_api.py:3::test_one": function},
        )
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"] = [
            razor_keep_item("alpha", "alpha/main.py:1-2"),
            razor_keep_item("alpha", "alpha/test_api.py:1-1"),
            razor_keep_item("alpha", "alpha/test_api.py:2-2"),
            razor_keep_item("alpha", "alpha/test_api.py:3-5"),
        ]
        self.write_reports(main, razor)
        self.assertEqual(self.run_check().returncode, 0)

        self.test_candidates["functions"]["alpha/test_api.py:3::test_one"]["file"] = "alpha/main.py"
        self.write_test_candidates()
        self.assert_candidate_error("file")

        wrong_name = {"classname": "test_api", "name": "test_other",
                      "file": "test_api.py", "line": "0"}
        large_row = {"classname": "test_api", "name": "test_large",
                     "file": "test_api.py", "line": "3"}
        self.add_pytest_measurement(
            "def test_one():\n    assert True\n\ndef test_large():\n    assert True\n",
            [wrong_name, large_row], [wrong_name, large_row],
            functions={
                "alpha/test_api.py:1::test_other": {
                    "file": "alpha/test_api.py", "line": 1,
                    "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
                },
                "alpha/test_api.py:4::test_large": {
                    "file": "alpha/test_api.py", "line": 4,
                    "covers": ["alpha/main.py:1-2"], "kills": ["mutant-1", "mutant-2"],
                },
            }, candidates=[{"function": "alpha/test_api.py:1::test_other",
                            "covered_by": ["alpha/test_api.py:4::test_large"]}],
        )
        self.assert_candidate_error("完整函数范围无法识别")

    def test_unittest_candidate_range_includes_its_first_decorator(self):
        source = (
            "import unittest\n"
            "class Checks(unittest.TestCase):\n"
            "    @staticmethod\n"
            "    def test_small():\n"
            "        assert True\n"
            "\n"
            "    @staticmethod\n"
            "    def test_large():\n"
            "        assert True\n"
        )
        rows = [
            {"classname": "Checks", "name": "test_small", "file": "test_api.py", "line": "2"},
            {"classname": "Checks", "name": "test_large", "file": "test_api.py", "line": "6"},
        ]
        small, large = "alpha/test_api.py:3::test_small", "alpha/test_api.py:7::test_large"
        functions = {
            small: {"file": "alpha/test_api.py", "line": 3,
                    "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"]},
            large: {"file": "alpha/test_api.py", "line": 7,
                    "covers": ["alpha/main.py:1-2"], "kills": ["mutant-1", "mutant-2"]},
        }
        self.add_pytest_measurement(
            source, rows, rows, functions=functions,
            candidates=[{"function": small, "covered_by": [large]}],
        )
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"] = [
            razor_keep_item("alpha", "alpha/main.py:1-2"),
            razor_keep_item("alpha", "alpha/test_api.py:1-1"),
            razor_keep_item("alpha", "alpha/test_api.py:2-2"),
            razor_keep_item("alpha", "alpha/test_api.py:3-5"),
            razor_keep_item("alpha", "alpha/test_api.py:7-9"),
        ]
        alpha["keep"][3]["cost"] += "非重复断言的见证在 alpha/test_api.py:7。"
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        alpha["keep"][3]["lines"] = "alpha/test_api.py:4-5"
        self.write_reports(main, razor)
        self.assert_candidate_error("完整范围")

    def test_nextest_function_file_line_pairs_are_unique_and_source_is_checked(self):
        source = "#[test]\nfn test_one() {\n}\n"
        item = {"ignored": False, "filter-match": {"status": "matches"}}
        listing = {"rust-suites": {
            "api": {"status": "listed", "testcases": {"tests::test_one": item}},
            "other": {"status": "listed", "testcases": {"tests::test_one": item}},
        }}
        functions = {
            "alpha::api::tests::test_one": {"file": "alpha/tests.rs", "line": 2,
                                     "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"]},
            "alpha::other::tests::test_one": {"file": "alpha/tests.rs", "line": 2,
                                     "covers": ["alpha/main.py:2-2"], "kills": ["mutant-2"]},
        }
        self.add_nextest_measurement(source, listing, {
            "api": [{"name": "tests::test_one"}],
            "other": [{"name": "tests::test_one"}],
        }, functions=functions)
        self.assert_candidate_error("相同")

    def test_failed_or_ignored_test_functions_are_unmeasured_and_inline_test_files_are_counted(self):
        source = "def test_one():\n    assert True\n"
        row = {"classname": "test_api", "name": "test_one", "file": "test_api.py", "line": "0"}
        self.add_pytest_measurement(source, [row], [{**row, "children": ["failure"]}])
        self.test_candidates["unmeasured_functions"] = [{
            "id": "alpha/test_api.py:1::test_one", "file": "alpha/test_api.py", "why": "失败。",
        }]
        self.write_test_candidates()
        self.assertEqual(self.run_check().returncode, 0)

        self.add_tracked_file("alpha/inline.rs", "#[test]\nfn inline_test() {}\n")
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"] = [
            razor_keep_item("alpha", "alpha/main.py:1-2"),
            razor_keep_item("alpha", "alpha/test_api.py:1-2"),
            razor_keep_item("alpha", "alpha/inline.rs:1-2"),
        ]
        self.write_reports(main, razor)
        self.write_test_candidates()
        self.assert_candidate_error("inline.rs")

        self.test_candidates["unmeasured_files"] = [{"path": "alpha/inline.rs", "why": "宏测试无法定位。"}]
        self.write_test_candidates()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_candidate_witness_must_cover_all_candidate_lines_and_mutants(self):
        source = (
            "def test_small():\n    assert True\n\n"
            "def test_first():\n    assert True\n\n"
            "def test_second():\n    assert True\n"
        )
        rows = [
            {"classname": "test_api", "name": "test_small", "file": "test_api.py", "line": "0"},
            {"classname": "test_api", "name": "test_first", "file": "test_api.py", "line": "3"},
            {"classname": "test_api", "name": "test_second", "file": "test_api.py", "line": "6"},
        ]
        small = "alpha/test_api.py:1::test_small"
        first = "alpha/test_api.py:4::test_first"
        second = "alpha/test_api.py:7::test_second"
        functions = {
            small: {"file": "alpha/test_api.py", "line": 1,
                    "covers": ["alpha/main.py:1-2"], "kills": ["mutant-1", "mutant-2"]},
            first: {"file": "alpha/test_api.py", "line": 4,
                    "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"]},
            second: {"file": "alpha/test_api.py", "line": 7,
                     "covers": ["alpha/main.py:2-2"], "kills": ["mutant-2"]},
        }
        self.add_pytest_measurement(
            source, rows, rows, functions=functions,
            candidates=[{"function": small, "covered_by": [first]}],
        )
        self.assert_candidate_error("covered_by 合起来")

        self.add_pytest_measurement(source, rows, rows, functions=functions,
                                    candidates=[{"function": small, "covered_by": [first, second]}])
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rust_inline_candidate_requires_full_function_keep_or_reduction(self):
        source = (
            "/// documentation\n#[test]\nfn test_small() {\n}\n\n"
            "#[test]\nfn test_large() {\n}\n"
        )
        listing = {"rust-suites": {"api": {"status": "listed", "testcases": {
            "test_small": {"ignored": False, "filter-match": {"status": "matches"}},
            "test_large": {"ignored": False, "filter-match": {"status": "matches"}},
        }}}}
        small, large = "alpha::api::test_small", "alpha::api::test_large"
        functions = {
            small: {"file": "alpha/inline.rs", "line": 3,
                    "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"]},
            large: {"file": "alpha/inline.rs", "line": 7,
                    "covers": ["alpha/main.py:1-2"], "kills": ["mutant-1", "mutant-2"]},
        }
        self.add_nextest_measurement(
            source, listing, {"api": [{"name": "test_small"}, {"name": "test_large"}]},
            functions=functions, candidates=[{"function": small, "covered_by": [large]}],
            test_path="alpha/inline.rs",
        )
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"] = [
            razor_keep_item("alpha", "alpha/main.py:1-2"),
            razor_keep_item("alpha", "alpha/inline.rs:1-2"),
            razor_keep_item("alpha", "alpha/inline.rs:6-8"),
        ]
        alpha["findings"] = [finding("删除")]
        alpha["findings"][0]["target"] = "alpha/inline.rs:3-4"
        self.write_reports(main, razor)
        self.assert_candidate_error("完整范围")

        alpha["keep"] = [
            razor_keep_item("alpha", "alpha/main.py:1-2"),
            razor_keep_item("alpha", "alpha/inline.rs:1-4"),
            razor_keep_item("alpha", "alpha/inline.rs:6-8"),
        ]
        alpha["findings"] = []
        alpha["keep"][1]["cost"] += "见证位置 alpha/inline.rs:7。"
        self.write_reports(main, razor)
        self.assertEqual(self.run_check().returncode, 0)

    def test_nextest_range_rejects_misformatted_close_and_accepts_full_feature_reduction(self):
        source = "#[test]\nfn test_small() {\n    body();\n }\n#[test]\nfn test_large() {\n}\n"
        listing = {"rust-suites": {"api": {"status": "listed", "testcases": {
            "test_small": {"ignored": False, "filter-match": {"status": "matches"}},
            "test_large": {"ignored": False, "filter-match": {"status": "matches"}},
        }}}}
        small, large = "alpha::api::test_small", "alpha::api::test_large"
        functions = {
            small: {"file": "alpha/tests.rs", "line": 2,
                    "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"]},
            large: {"file": "alpha/tests.rs", "line": 6,
                    "covers": ["alpha/main.py:1-2"], "kills": ["mutant-1", "mutant-2"]},
        }
        self.add_nextest_measurement(
            source, listing, {"api": [{"name": "test_small"}, {"name": "test_large"}]},
            functions=functions, candidates=[{"function": small, "covered_by": [large]}],
        )
        self.assert_candidate_error("范围")

        self.test_candidates["functions"].pop(small)
        self.test_candidates["unmeasured_functions"] = [{
            "id": small, "file": "alpha/tests.rs", "why": "Rust 函数范围没有按 rustfmt 缩进。",
        }]
        self.test_candidates["candidates"] = []
        self.write_test_candidates()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        source = "#[test]\nfn test_small() {\n}\n#[test]\nfn test_large() {\n}\n"
        functions[small] = {
            "file": "alpha/tests.rs", "line": 2,
            "covers": ["alpha/main.py:1-1"], "kills": ["mutant-1"],
        }
        functions[large]["line"] = 5
        self.add_nextest_measurement(
            source, listing, {"api": [{"name": "test_small"}, {"name": "test_large"}]},
            functions=functions, candidates=[{"function": small, "covered_by": [large]}],
        )
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"] = [razor_keep_item("alpha", "alpha/main.py:1-2")]
        alpha["findings"] = [finding("合并")]
        alpha["findings"][0]["target"] = "alpha"
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def set_measured_usage(self, missed=("1-1",)):
        self.usage_coverage = {
            "sources": [{
                "source": "README.md:5-5",
                "scenarios": [{
                    "command": "python3 demo.py",
                    "ran": True,
                    "why_not": "",
                }],
                "why_none": "",
            }],
            "files": {
                "alpha/main.py": {"missed": list(missed)},
                "beta/main.py": {"missed": []},
            },
            "unmeasured": [],
        }

    def set_missed_candidate(self, verdict="用到", evidence="参考 README.md:1"):
        self.set_measured_usage()
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["usage_candidates"] = [{
            "lines": "alpha/main.py:1-1",
            "verdict": verdict,
            "evidence": evidence,
        }]
        return razor

    def write_source_fixture(self, text, sources):
        self.add_tracked_file("README.md", text)
        self.usage_coverage = {
            "sources": sources,
            "files": {},
            "unmeasured": [
                {"path": "alpha/main.py", "why": "没有可安全运行的场景。"},
                {"path": "beta/main.py", "why": "没有可安全运行的场景。"},
            ],
        }

    def test_usage_candidate_requires_one_entry_for_each_missed_range(self):
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        self.set_measured_usage()
        alpha["usage_candidates"] = []
        self.write_reports(report("主审", self.repository), razor)
        self.run_check_and_assert("usage_candidates 缺少实际使用候选")

        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        candidate = {
            "lines": "alpha/main.py:1-1",
            "verdict": "用到",
            "evidence": "参考 README.md:1",
        }
        alpha["usage_candidates"] = [candidate, candidate.copy()]
        self.write_reports(report("主审", self.repository), razor)
        self.run_check_and_assert("usage_candidates 的候选必须恰有一条")

    def test_usage_candidate_used_verdict_needs_valid_external_evidence(self):
        razor = self.set_missed_candidate(evidence="用户确实会调用这个功能")
        self.write_reports(report("主审", self.repository), razor)
        self.run_check_and_assert("evidence 必须引至少一处有效位置")

    def test_usage_candidate_delete_verdict_needs_a_matching_finding(self):
        razor = self.set_missed_candidate(verdict="删")
        self.write_reports(report("主审", self.repository), razor)
        self.run_check_and_assert("判删须有重叠发现或功能级删除、合并发现")

    def test_usage_candidate_delete_verdict_accepts_overlapping_finding(self):
        razor = self.set_missed_candidate(verdict="删")
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["findings"] = [finding()]
        self.write_reports(report("主审", self.repository), razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_usage_candidates_reject_entries_outside_missed_ranges(self):
        self.set_measured_usage(missed=())
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["usage_candidates"] = [{
            "lines": "alpha/main.py:1-1",
            "verdict": "用到",
            "evidence": "参考 README.md:1",
        }]
        self.write_reports(report("主审", self.repository), razor)
        self.run_check_and_assert("usage_candidates 多出不在 usage-coverage missed 里的候选")

    def test_usage_sources_match_readme_tables_lists_and_paragraphs(self):
        cases = (
            (
                "# Demo\n\n## 实际使用\n\n| 来源 | 说明 |\n| --- | --- |\n| one | first |\n| two | second |\n",
                ("README.md:7-7", "README.md:8-8"),
            ),
            (
                "# Demo\n\n## 实际使用\n- first source\n- second source\n",
                ("README.md:4-4", "README.md:5-5"),
            ),
            (
                "# Demo\n\n## 实际使用\nfirst paragraph\n\nsecond paragraph\n",
                ("README.md:4-4", "README.md:6-6"),
            ),
            (
                "# Demo\n\n实际使用\n---\nfirst paragraph\n\nsecond paragraph\n",
                ("README.md:5-5", "README.md:7-7"),
            ),
        )
        for readme, expected in cases:
            with self.subTest(expected=expected):
                self.write_source_fixture(readme, [
                    {"source": source, "scenarios": [], "why_none": "无法推出可运行场景。"}
                    for source in expected
                ])
                self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
                result = self.run_check()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

                invalid_sources = (
                    [
                        {"source": expected[0], "scenarios": [], "why_none": "无法推出可运行场景。"}
                    ],
                    [
                        {"source": f"README.md:{expected[0].split(':')[1].split('-')[0]}-"
                         f"{expected[1].split(':')[1].split('-')[1]}",
                         "scenarios": [], "why_none": "无法推出可运行场景。"}
                    ],
                    [
                        *[
                            {"source": source, "scenarios": [], "why_none": "无法推出可运行场景。"}
                            for source in expected
                        ],
                        {"source": "README.md:1-1", "scenarios": [],
                         "why_none": "多出的来源。"},
                    ],
                )
                for sources in invalid_sources:
                    self.write_source_fixture(readme, sources)
                    self.write_reports(report("主审", self.repository),
                                       report("剃刀", self.repository))
                    result = self.run_check()
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("sources", result.stdout)

    def test_usage_sources_require_readme_actual_usage_section(self):
        self.write_source_fixture("# Demo\n\nNo usage section.\n", [])
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        self.run_check_and_assert("README.md 缺少「实际使用」一节")

    def test_usage_source_scenarios_and_why_none_must_agree(self):
        for scenarios, why_none in (([], ""), ([{
            "command": "python3 demo.py", "ran": False, "why_not": "副本缺少配置。",
        }], "仍填写 why_none。")):
            with self.subTest(scenarios=scenarios, why_none=why_none):
                self.usage_coverage["sources"][0]["scenarios"] = scenarios
                self.usage_coverage["sources"][0]["why_none"] = why_none
                self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
                self.run_check_and_assert("scenarios" + (" 为空时" if not scenarios else " 非空时"))

    def test_usage_files_must_be_empty_when_no_scenario_ran(self):
        self.usage_coverage["files"] = {"alpha/main.py": {"missed": ["1-1"]}}
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        self.run_check_and_assert("没有 ran 为 true 的场景时 files 必须是空对象")

    def test_usage_missed_ranges_reject_adjacency_but_allow_executed_gap(self):
        self.add_tracked_file("alpha/main.py", "first = 1\nsecond = 2\nthird = 3\n")
        self.set_measured_usage(missed=("1-1", "2-2"))

        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = [razor_keep_item("alpha", "alpha/main.py:1-3")]
        self.write_reports(report("主审", self.repository), razor)
        self.run_check_and_assert("相邻的 missed 行应合并成一个范围")

        self.set_measured_usage(missed=("1-1", "3-3"))
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["keep"] = [razor_keep_item("alpha", "alpha/main.py:1-3")]
        alpha["usage_candidates"] = [
            {
                "lines": f"alpha/main.py:{line_range}",
                "verdict": "用到",
                "evidence": "参考 README.md:1",
            }
            for line_range in ("1-1", "3-3")
        ]
        self.write_reports(report("主审", self.repository), razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_direct_code_file_must_be_measured_or_unmeasured(self):
        self.usage_coverage["unmeasured"] = [
            item for item in self.usage_coverage["unmeasured"]
            if item["path"] != "alpha/main.py"
        ]
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        self.run_check_and_assert("直属代码文件必须恰好出现在 files 或 unmeasured")

    def test_usage_coverage_is_required_and_must_be_readable_json(self):
        result = self.run_check(usage_coverage=False)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--usage-coverage", result.stderr)

        self.usage_coverage_path.write_text("{bad json", encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("usage-coverage", result.stdout + result.stderr)

    def test_usage_coverage_requires_an_existing_file_and_exact_top_level_shape(self):
        self.usage_coverage_path.unlink()
        result = self.run_check()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--usage-coverage 必须指向已有文件", result.stderr)

        self.usage_coverage_path.write_text("[]", encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("usage-coverage必须是对象", result.stdout)

    def test_usage_candidate_fields_and_verdict_are_restricted(self):
        self.set_measured_usage()
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["usage_candidates"] = [{
            "lines": "alpha/main.py:1-1",
            "verdict": "用到",
            "evidence": "",
            "extra": True,
        }]
        self.write_reports(report("主审", self.repository), razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("多余字段 extra", result.stdout)

        alpha["usage_candidates"] = [{
            "lines": "alpha/main.py:1-1",
            "verdict": "keep",
            "evidence": "",
        }]
        self.write_reports(report("主审", self.repository), razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("verdict 只能是 用到 或 删", result.stdout)

    def test_razor_requires_rewrites_for_each_direct_document(self):
        razor = report("剃刀", self.repository)
        root = next(item for item in razor["features"] if item["feature"] == "root")
        del root["rewrites"]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("缺少字段 rewrites", result.stdout + result.stderr)

    def test_rewrite_paths_and_item_fields_must_match_direct_documents(self):
        self.add_tracked_file("alpha/guide.md", "Original guide.\n")
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [{"lines": "alpha/main.py:1-1", "text": "value = 1"}]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("rewrites", output)
        self.assertIn("alpha/guide.md", output)
        self.assertIn("alpha/main.py", output)

        for rewrite, expected in (
            ({"lines": "alpha/guide.md:1-1"}, "缺少字段 text"),
            ({"lines": "alpha/guide.md:1-1", "text": "Original guide.\n", "extra": 1}, "多余字段 extra"),
            ({"lines": "alpha/guide.md:1-1", "text": 7}, "text 必须是字符串"),
        ):
            with self.subTest(rewrite=rewrite):
                fresh = report("剃刀", self.repository)
                fresh_alpha = next(
                    item for item in fresh["features"] if item["feature"] == "alpha"
                )
                fresh_alpha["rewrites"] = [rewrite]
                self.write_reports(report("主审", self.repository), fresh)
                checked = self.run_check()
                self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn(expected, checked.stdout + checked.stderr)

    def test_rewrite_blocks_cover_every_nonempty_line_without_overlap(self):
        self.add_tracked_file("alpha/guide.md", "First paragraph.\n\nSecond paragraph.\nThird paragraph.\n")
        cases = (
            (
                [
                    {"lines": "alpha/guide.md:1-1", "text": "First paragraph."},
                    {"lines": "alpha/guide.md:4-4", "text": "Third paragraph."},
                ],
                "未覆盖直属文档改写的非空行：alpha/guide.md:3",
            ),
            (
                [
                    {"lines": "alpha/guide.md:1-3", "text": "First and second."},
                    {"lines": "alpha/guide.md:3-4", "text": "Second and third."},
                ],
                "行范围重叠",
            ),
        )
        for rewrites, expected in cases:
            with self.subTest(expected=expected):
                razor = report("剃刀", self.repository)
                alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
                alpha["rewrites"] = rewrites
                self.write_reports(report("主审", self.repository), razor)

                result = self.run_check()

                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout + result.stderr)

    def test_rewrite_blocks_must_be_sorted_by_start_line(self):
        self.add_tracked_file("alpha/guide.md", "First paragraph.\nSecond paragraph.\n")
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [
            {"lines": "alpha/guide.md:2-2", "text": "Second paragraph."},
            {"lines": "alpha/guide.md:1-1", "text": "First paragraph."},
        ]
        alpha["keep"].append(razor_keep_item("alpha", "alpha/guide.md:1-2"))
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("起始行递增", result.stdout + result.stderr)

    def test_rewrite_ranges_must_be_valid_and_belong_to_a_direct_document(self):
        self.add_tracked_file("alpha/guide.md", "First paragraph.\nSecond paragraph.\n")
        cases = (
            ([{"lines": "alpha/guide.md:1", "text": "First."}], "必须写成 路径:起始行-结束行"),
            ([{"lines": "alpha/guide.md:0-1", "text": "First."}], "行范围无效"),
            ([{"lines": "alpha/guide.md:1-3", "text": "Both."}], "行范围无效"),
            ([{"lines": "alpha/main.py:1-1", "text": "Code."}], "文件不在允许范围内"),
        )
        for rewrites, expected in cases:
            with self.subTest(rewrites=rewrites):
                razor = report("剃刀", self.repository)
                alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
                alpha["rewrites"] = rewrites
                self.write_reports(report("主审", self.repository), razor)

                result = self.run_check()

                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout + result.stderr)

    def test_rewrite_rejects_a_table_with_a_missing_first_cell(self):
        original = "| Stage | Action | Note |\n|---|---|---|\n| Plan | Draft | Review |\n"
        rewritten = "| Action | Note |\n|---|---|\n| Draft | Review |\n"

        result = self.check_guide_rewrites(
            original, [{"lines": "alpha/guide.md:1-3", "text": rewritten}],
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("表格", output)
        self.assertIn("alpha/guide.md", output)
        self.assertIn("alpha/guide.md:1-3", output)

    def test_rewrite_rejects_a_table_with_a_missing_column(self):
        original = "| Stage | Action | Note |\n|---|---|---|\n| Plan | Draft | Review |\n"
        rewritten = "| Stage | Action |\n|---|---|\n| Plan | Draft |\n"

        result = self.check_guide_rewrites(
            original, [{"lines": "alpha/guide.md:1-3", "text": rewritten}],
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("表格", output)
        self.assertIn("alpha/guide.md", output)
        self.assertIn("alpha/guide.md:1-3", output)

    def test_rewrite_rejects_a_table_with_a_deleted_data_row(self):
        original = (
            "| Stage | Action |\n|---|---|\n| Plan | Draft |\n"
            "| Review | Check |\n| Report | Share |\n"
        )
        rewritten = (
            "| Stage | Action |\n|---|---|\n| Plan | Draft |\n"
            "| Report | Share |\n"
        )

        result = self.check_guide_rewrites(
            original, [{"lines": "alpha/guide.md:1-5", "text": rewritten}],
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("表格", output)
        self.assertIn("alpha/guide.md", output)
        self.assertIn("alpha/guide.md:1-5", output)

    def test_rewrite_accepts_a_table_when_other_cells_change(self):
        original = "| Stage | Before |\n|---|---|\n| Plan | Fixed |\n"
        rewritten = "| Stage | Change |\n|---|---|\n| Plan | Other |\n"

        result = self.check_guide_rewrites(
            original, [{"lines": "alpha/guide.md:1-3", "text": rewritten}],
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rewrite_checks_tables_without_outer_pipes(self):
        original = "Stage | Action | Note\n---|---|---\nPlan | Draft | Review\n"
        cases = (
            "Action | Note\n---|---\nDraft | Review\n",
            "Stage | Action\n---|---\nPlan | Draft\n",
        )

        for rewritten in cases:
            with self.subTest(rewritten=rewritten):
                result = self.check_guide_rewrites(
                    original, [{"lines": "alpha/guide.md:1-3", "text": rewritten}],
                )
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn("表格", output)
                self.assertIn("alpha/guide.md:1-3", output)

    def test_rewrite_checks_indented_tables(self):
        original = "  Stage | Action | Note\n  ---|---|---\n  Plan | Draft | Review\n"
        cases = (
            "Action | Note\n---|---\nDraft | Review\n",
            "Stage | Action\n---|---\nPlan | Draft\n",
        )

        for rewritten in cases:
            with self.subTest(rewritten=rewritten):
                result = self.check_guide_rewrites(
                    original, [{"lines": "alpha/guide.md:1-3", "text": rewritten}],
                )
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn("表格", output)
                self.assertIn("alpha/guide.md:1-3", output)

    def test_table_rows_must_stay_in_one_rewrite_block(self):
        original = (
            "Opening.\n| Stage | Action |\n|---|---|\n"
            "| Plan | Draft |\n| Review | Check |\nClosing.\n"
        )
        rewrites = [
            {"lines": "alpha/guide.md:1-3", "text": "Opening.\n| Stage | Action |\n|---|---|"},
            {"lines": "alpha/guide.md:4-6", "text": "| Plan | Draft |\n| Review | Check |\nClosing."},
        ]

        result = self.check_guide_rewrites(original, rewrites)

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("表格", output)
        self.assertIn("alpha/guide.md:2-5", output)
        self.assertIn("alpha/guide.md:1-3", output)
        self.assertIn("alpha/guide.md:4-6", output)

    def test_wholly_deleted_rewrite_block_skips_table_check(self):
        original = "| Stage | Action |\n|---|---|\n| Plan | Draft |\n"

        result = self.check_guide_rewrites(
            original, [{"lines": "alpha/guide.md:1-3", "text": ""}],
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fenced_code_tables_are_not_compared(self):
        for fence in ("```", "~~~"):
            with self.subTest(fence=fence):
                table = (
                    f"{fence}md\n| Stage | Action |\n|---|---|\n"
                    f"| Plan | Draft |\n{fence}"
                )
                shortened = f"{fence}md\n| Stage | Action |\n|---|---|\n{fence}"
                original = f"Start.\n{table}\nMiddle.\n{table}\nEnd.\n"
                rewritten = (
                    f"Start.\n{shortened}\nThis explanation is long enough to keep the rewrite from being shorter.\n"
                    f"Middle.\n{table}\nEnd."
                )
                result = self.check_guide_rewrites(
                    original,
                    [{"lines": f"alpha/guide.md:1-{len(original.splitlines())}", "text": rewritten}],
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fenced_code_table_rows_do_not_trigger_cross_block_error(self):
        for fence in ("```", "~~~"):
            with self.subTest(fence=fence):
                original = (
                    f"{fence}md\n| Stage | Action |\n|---|---|\n"
                    f"| Plan | Draft |\n{fence}\n"
                )
                lines = original.splitlines()
                rewrites = [
                    {"lines": "alpha/guide.md:1-2", "text": "\n".join(lines[:2])},
                    {"lines": f"alpha/guide.md:3-{len(lines)}", "text": "\n".join(lines[2:])},
                ]

                result = self.check_guide_rewrites(original, rewrites)

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_list_item_fenced_code_tables_do_not_trigger_cross_block_error(self):
        original = (
            "- ```md\n  | Stage | Action |\n  |---|---|\n"
            "  | Plan | Draft |\n  ```\n"
        )
        lines = original.splitlines()
        rewrites = [
            {"lines": "alpha/guide.md:1-3", "text": "\n".join(lines[:3])},
            {"lines": "alpha/guide.md:4-5", "text": "\n".join(lines[3:])},
        ]

        result = self.check_guide_rewrites(original, rewrites)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_single_column_trailing_pipe_table_rejects_a_deleted_data_row(self):
        original = "Stage |\n---|\nPlan |\nReview |\n"
        rewritten = "Stage |\n---|\nReview |\n"

        result = self.check_guide_rewrites(
            original, [{"lines": "alpha/guide.md:1-4", "text": rewritten}],
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("Markdown 表格", output)
        self.assertIn("alpha/guide.md:1-4", output)

    def test_single_column_trailing_pipe_table_rejects_a_missing_first_cell(self):
        original = "Stage |\n---|\nPlan |\n"
        rewritten = " |\n---|\n |\n"

        result = self.check_guide_rewrites(
            original, [{"lines": "alpha/guide.md:1-3", "text": rewritten}],
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("Markdown 表格", output)
        self.assertIn("alpha/guide.md:1-3", output)

    def test_rewrite_keeps_each_backtick_span_verbatim(self):
        self.add_tracked_file("alpha/guide.md", "Run `tool --flag` now.\n")
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [{
            "lines": "alpha/guide.md:1-1",
            "text": "Run tool --flag now.",
        }]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("`tool --flag`", result.stdout + result.stderr)

    def test_split_code_blocks_with_changed_code_are_rejected(self):
        cases = (
            (
                "bare-fence.md",
                "```sh\necho x\n```\n",
                "echo y",
            ),
            (
                "quoted-fence.md",
                "> ```sh\n> echo x\n> ```\n",
                "> echo y",
            ),
            (
                "list-fence.md",
                "- ```sh\n  echo x\n  ```\n",
                "  echo y",
            ),
        )
        for name, content, changed_code in cases:
            with self.subTest(name=name):
                path = f"alpha/{name}"
                self.add_tracked_file(path, content)
                razor = report("剃刀", self.repository)
                alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
                source_lines = content.splitlines()
                alpha["rewrites"] = [
                    {"lines": f"{path}:{line}-{line}",
                     "text": changed_code if line == 2 else text}
                    for line, text in enumerate(source_lines, start=1)
                ]
                for candidate, _content, _changed in cases:
                    candidate_path = self.repository / "alpha" / candidate
                    if candidate_path.exists():
                        line_count = len(candidate_path.read_text(encoding="utf-8").splitlines())
                        alpha["keep"].append(
                            razor_keep_item("alpha", f"alpha/{candidate}:1-{line_count}")
                        )
                self.write_reports(report("主审", self.repository), razor)

                result = self.run_check()

                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn("缺少原文反引号片段", output)

    def test_changed_tilde_fenced_code_is_rejected(self):
        self.add_tracked_file("alpha/guide.md", "~~~sh\necho x\n~~~\n")
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [
            {"lines": "alpha/guide.md:1-1", "text": "~~~sh"},
            {"lines": "alpha/guide.md:2-2", "text": "echo y"},
            {"lines": "alpha/guide.md:3-3", "text": "~~~"},
        ]
        alpha["keep"].append(razor_keep_item("alpha", "alpha/guide.md:1-3"))
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("缺少原文反引号片段", output)

    def test_unchanged_tilde_fenced_code_is_allowed(self):
        content = "~~~sh\necho x\n~~~\n"
        self.add_tracked_file("alpha/guide.md", content)
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [
            {"lines": f"alpha/guide.md:{line}-{line}", "text": text}
            for line, text in enumerate(content.splitlines(), start=1)
        ]
        alpha["keep"].append(razor_keep_item("alpha", "alpha/guide.md:1-3"))
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_duplicate_backtick_fragment_may_be_kept_once(self):
        self.add_tracked_file(
            "alpha/guide.md",
            "Use `why.source` twice and then `why.source` again.\n",
        )
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [{
            "lines": "alpha/guide.md:1-1",
            "text": "Use `why.source` once.\n",
        }]
        rewrite_finding = finding("重构", True, "问你")
        rewrite_finding["target"] = "alpha/guide.md:1-1"
        alpha["findings"] = [rewrite_finding]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_duplicate_backtick_fragment_missing_from_rewrite_is_rejected(self):
        self.add_tracked_file(
            "alpha/guide.md",
            "Use `why.source` twice and then `why.source` again.\n",
        )
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [{
            "lines": "alpha/guide.md:1-1",
            "text": "Use the property once.\n",
        }]
        rewrite_finding = finding("重构", True, "问你")
        rewrite_finding["target"] = "alpha/guide.md:1-1"
        alpha["findings"] = [rewrite_finding]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("缺少原文反引号片段", output)
        self.assertIn("`why.source`", output)

    def test_backtick_fragment_inside_a_wholly_deleted_block_is_allowed(self):
        self.add_tracked_file("alpha/guide.md", "Run `obsolete-tool` today.\n")
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [{"lines": "alpha/guide.md:1-1", "text": ""}]
        removal = finding("删除", False, "直接做")
        removal["target"] = "alpha/guide.md:1-1"
        alpha["findings"] = [removal]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_backtick_fragment_crossing_a_deleted_block_is_still_required(self):
        self.add_tracked_file("alpha/guide.md", "Use `tool\ncommand` now.\n")
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [
            {"lines": "alpha/guide.md:1-1", "text": ""},
            {"lines": "alpha/guide.md:2-2", "text": "command now."},
        ]
        rewrite_finding = finding("重构", True, "问你")
        rewrite_finding["target"] = "alpha/guide.md:1-2"
        alpha["findings"] = [rewrite_finding]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("缺少原文反引号片段", output)

    def test_shorter_rewrite_needs_a_whole_file_finding_and_reports_character_counts(self):
        self.add_tracked_file("alpha/guide.md", "Long original words.\n")
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [{"lines": "alpha/guide.md:1-1", "text": "Short."}]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("alpha/guide.md:1-1", output)
        self.assertIn("18", output)
        self.assertIn("6", output)

    def test_rewrite_character_count_sums_each_block(self):
        self.add_tracked_file("alpha/guide.md", "Original first paragraph.\nOriginal second paragraph.\n")
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [
            {"lines": "alpha/guide.md:1-1", "text": "Short."},
            {"lines": "alpha/guide.md:2-2", "text": "Tiny."},
        ]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("alpha/guide.md:1-2", output)
        self.assertIn(str(character_count("Original first paragraph.") + character_count(
            "Original second paragraph."
        )), output)
        self.assertIn(str(character_count("Short.") + character_count("Tiny.")), output)

    def test_rewrite_finding_kind_matches_empty_or_nonempty_text(self):
        self.add_tracked_file("alpha/guide.md", "Long original words.\n")
        for text, kind, expected in (
            ("Short.", "删除", "必须是 重构"),
            ("", "重构", "必须是 删除"),
        ):
            with self.subTest(text=text, kind=kind):
                razor = report("剃刀", self.repository)
                alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
                alpha["rewrites"] = [{"lines": "alpha/guide.md:1-1", "text": text}]
                rewrite_finding = finding(kind, True, "问你")
                rewrite_finding["target"] = "alpha/guide.md:1-1"
                alpha["findings"] = [rewrite_finding]
                self.write_reports(report("主审", self.repository), razor)

                result = self.run_check()

                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout + result.stderr)

    def test_same_length_rewrite_needs_keep_but_no_finding(self):
        self.add_tracked_file("alpha/guide.md", "Original guide.\n")
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [{"lines": "alpha/guide.md:1-1", "text": "Original guide."}]
        alpha["keep"].append(razor_keep_item("alpha", "alpha/guide.md:1-1"))
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shorter_rewrite_finding_covers_file_without_keep(self):
        self.add_tracked_file("alpha/guide.md", "Long original words.\n")
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        alpha["rewrites"] = [{"lines": "alpha/guide.md:1-1", "text": "Short."}]
        rewrite_finding = finding("重构", True, "问你")
        rewrite_finding["target"] = "alpha/guide.md:1-1"
        alpha["findings"] = [rewrite_finding]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shorter_rewrite_does_not_exempt_another_keep_from_recheck(self):
        self.add_tracked_file("alpha/guide.md", "Long original words.\n")
        main = report("主审", self.repository, ("alpha",))
        razor = report("剃刀", self.repository, ("alpha",))
        alpha = razor["features"][0]
        alpha["rewrites"] = [{"lines": "alpha/guide.md:1-1", "text": "Short."}]
        rewrite_finding = finding("重构", True, "问你")
        rewrite_finding["target"] = "alpha/guide.md:1-1"
        alpha["findings"] = [rewrite_finding]
        self.write_reports(main, razor)
        self.clear_recheck_reports()

        result = self.run_check(scope=("alpha",))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("缺剃刀复审：alpha", result.stdout)

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

    def test_finding_target_cannot_be_in_a_skipped_file(self):
        self.add_tracked_file("alpha/guide.md", "Skipped guide.\n")
        self.add_tracked_file("repo-map/skip.json", json.dumps(["alpha/guide.md"]))
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        skipped_finding = finding("重构", True, "问你")
        skipped_finding["target"] = "alpha/guide.md:1-1"
        alpha["findings"] = [skipped_finding]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("跳过名单", result.stdout + result.stderr)

    def test_skipped_finding_target_is_rejected_before_reading_its_file(self):
        target_path = self.repository / "alpha" / "guide.md"
        self.add_tracked_file("alpha/guide.md", "Skipped guide.\n")
        self.add_tracked_file("repo-map/skip.json", json.dumps(["alpha/guide.md"]))
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        skipped_finding = finding("重构", True, "问你")
        skipped_finding["target"] = "alpha/guide.md:1-1"
        alpha["findings"] = [skipped_finding]
        self.write_reports(report("主审", self.repository), razor)
        data = check_reports_module.build_map(self.repository)
        tracked_files = tracked_paths(self.repository)
        original_read = check_reports_module._read_file_lines
        target_reads = []

        def record_reads(file_path):
            if Path(file_path) == target_path:
                target_reads.append(file_path)
            return original_read(file_path)

        with patch.object(check_reports_module, "_read_file_lines", side_effect=record_reads):
            problems = check_reports_module.validate_reports(
                data,
                self.repository,
                set(data["features"]),
                self.reports_for_check(),
                tracked_files,
                self.usage_coverage,
                {},
                [],
            )

        self.assertTrue(any("跳过名单" in problem for problem in problems), problems)
        self.assertEqual(target_reads, [])

    def test_skipped_todo_target_is_not_validated_as_a_todo_finding(self):
        self.add_tracked_file("alpha/TODO.md", "Delete this skipped reminder.\n")
        self.add_tracked_file("repo-map/skip.json", json.dumps(["alpha/TODO.md"]))
        razor = report("剃刀", self.repository)
        alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
        skipped_todo_finding = finding("删除", False, "直接做")
        skipped_todo_finding["target"] = "alpha/TODO.md:1-1"
        alpha["findings"] = [skipped_todo_finding]
        self.write_reports(report("主审", self.repository), razor)

        result = self.run_check()

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("跳过名单", output)
        self.assertNotIn("目标在 TODO 文件", output)

    def test_razor_requires_necessity(self):
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        del alpha["necessity"]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("缺少字段 necessity", result.stdout)

    def test_razor_necessity_must_be_nonempty(self):
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["necessity"] = "  "
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("features[1].necessity 必须是非空字符串", result.stdout)

    def test_razor_requires_smallest(self):
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
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
                main, razor, alpha = reports_for_feature(self.repository, "alpha")
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
                main, razor, alpha = reports_for_feature(self.repository, "alpha")
                if name not in {"equal estimate", "higher estimate"}:
                    alpha["smallest"]["lines"]["code"] = 0
                else:
                    alpha["smallest"]["lines"]["code"] = 1 if name == "equal estimate" else 2
                if candidate is not None:
                    kind, target = candidate
                    proposal = finding(
                        kind, changes_behavior=False,
                        decision="直接做",
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
                    proposal = finding(
                        kind, False,
                        "直接做" if kind in {"删除", "合并"} else "问你",
                    )
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"] = [
            razor_keep_item("alpha"),
            razor_keep_item("alpha", "alpha/first.md:1-6"),
        ]
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
        challenge = finding("删除", changes_behavior=False, decision="直接做")
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
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
                self.usage_coverage,
                {},
                [],
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"] = []
        deletion = finding("删除", False, "直接做")
        deletion["target"] = "alpha"
        alpha["findings"] = [deletion]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_feature_id_reduction_with_todo_must_ask_user(self):
        self.add_tracked_file("alpha/TODO.md", "待办事项。\n")
        for kind in ("删除", "合并"):
            with self.subTest(kind=kind):
                main = report("主审", self.repository)
                razor = report("剃刀", self.repository)
                for document in (main, razor):
                    alpha = next(item for item in document["features"] if item["feature"] == "alpha")
                    alpha["todo_items"] = [todo_item("alpha/TODO.md:1-1")]
                alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
                alpha["keep"] = []
                reduction = finding(kind, False, "直接做")
                reduction["target"] = "alpha"
                alpha["findings"] = [reduction]
                self.write_reports(main, razor)

                result = self.run_check()

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("会影响直属 TODO", result.stdout)

    def test_feature_id_reduction_must_cover_affected_todo_lines_with_ask_findings(self):
        self.add_tracked_file("alpha/TODO.md", "待办事项。\n")
        for kind in ("删除", "合并"):
            with self.subTest(kind=kind):
                main = report("主审", self.repository)
                razor = report("剃刀", self.repository)
                for document in (main, razor):
                    alpha = next(item for item in document["features"] if item["feature"] == "alpha")
                    alpha["todo_items"] = [todo_item("alpha/TODO.md:1-1")]
                alpha = next(item for item in razor["features"] if item["feature"] == "alpha")
                alpha["keep"] = []
                reduction = finding(kind, True, "问你")
                reduction["target"] = "alpha"
                alpha["findings"] = [reduction]
                self.write_reports(main, razor)

                result = self.run_check()

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("直属 TODO", result.stdout)
                self.assertIn("alpha/TODO.md:1", result.stdout)

    def test_missing_recheck_report_lists_each_required_feature(self):
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        self.clear_recheck_reports()
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("缺剃刀复审", result.stdout)
        for feature_id in ("root", "alpha", "beta"):
            self.assertIn(feature_id, result.stdout)

    def test_first_round_deletion_does_not_exempt_a_kept_feature_from_recheck(self):
        self.add_tracked_file("alpha/extra.py", "value = 3\n")
        main = report("主审", self.repository, ("alpha",))
        razor = report("剃刀", self.repository, ("alpha",))
        alpha = razor["features"][0]
        deletion = finding("删除", False, "直接做")
        deletion["target"] = "alpha/extra.py:1"
        alpha["findings"] = [deletion]
        self.write_reports(main, razor)
        self.clear_recheck_reports()

        result = self.run_check(scope=("alpha",))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("缺剃刀复审：alpha", result.stdout)

    def test_recheck_is_required_for_a_keep_even_when_first_round_refactors(self):
        main, razor = self.recheck_scenario()
        self.write_reports(main, razor)
        self.write_recheck_reports([recheck_report(self.repository, ["alpha"])])
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
        self.write_recheck_reports([recheck_report(self.repository, [])])
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
        self.write_recheck_reports([recheck_report(self.repository, ["alpha"])])
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("不用复审", result.stdout)
        self.assertIn("alpha", result.stdout)

    def test_feature_with_no_keep_files_does_not_require_recheck(self):
        map_path = self.repository / "repo-map" / "features.json"
        feature_map = json.loads(map_path.read_text(encoding="utf-8"))
        feature_map["children"].append(feature("empty", None))
        self.add_tracked_file(
            "repo-map/features.json", json.dumps(feature_map, ensure_ascii=False)
        )
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

    def test_recheck_findings_use_the_same_decisions_as_first_pass_findings(self):
        valid = [
            finding("删除", False, "直接做"),
            finding("合并", False, "直接做"),
            finding("删除", True, "问你"),
            finding("重构", False, "直接做"),
        ]
        for item in valid:
            with self.subTest(kind=item["kind"], behavior=item["changes_behavior"]):
                main = report("主审", self.repository)
                razor = report("剃刀", self.repository)
                self.write_reports(main, razor)
                rechecks = self.read_recheck_reports()
                recheck = next(
                    document for document in rechecks
                    if document["features"][0]["feature"] == "alpha"
                )
                recheck["features"][0]["findings"] = [item]
                self.write_recheck_reports(rechecks)
                result = self.run_check(reports=self.reports_for_check())
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        invalid = [
            finding("删除", False, "问你"),
            finding("删除", True, "直接做"),
            finding("重构", False, "问你"),
        ]
        for item in invalid:
            with self.subTest(kind=item["kind"], behavior=item["changes_behavior"],
                              decision=item["decision"]):
                main = report("主审", self.repository)
                razor = report("剃刀", self.repository)
                self.write_reports(main, razor)
                rechecks = self.read_recheck_reports()
                recheck = next(
                    document for document in rechecks
                    if document["features"][0]["feature"] == "alpha"
                )
                recheck["features"][0]["findings"] = [item]
                self.write_recheck_reports(rechecks)
                result = self.run_check(reports=self.reports_for_check())
                self.assertEqual(result.returncode, 1)
                self.assertIn("decision", result.stdout)

    def test_recheck_feature_rejects_razor_only_fields(self):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        recheck = recheck_report(self.repository, ["alpha"])
        recheck["features"][0]["keep"] = []
        self.write_reports(main, razor)
        self.write_recheck_reports([recheck])
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
        self.write_recheck_reports([recheck])
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/main.py:2-2", result.stdout)

    def test_recheck_rejects_duplicate_verdicts_for_a_keep(self):
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        recheck = self.read_recheck_for_feature("alpha")
        alpha = recheck["features"][0]
        alpha["verdicts"].append(dict(alpha["verdicts"][0]))
        self.write_recheck_reports([recheck])
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("重复", result.stdout)
        self.assertIn("alpha/main.py:1-1", result.stdout)

    def test_recheck_rejects_empty_attempt(self):
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        recheck = self.read_recheck_for_feature("alpha")
        alpha = recheck["features"][0]
        alpha["verdicts"][0]["attempt"] = "  "
        self.write_recheck_reports([recheck])
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("attempt", result.stdout)

    def test_recheck_rejects_unknown_verdict_value(self):
        self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
        recheck = self.read_recheck_for_feature("alpha")
        alpha = recheck["features"][0]
        alpha["verdicts"][0]["verdict"] = "待定"
        self.write_recheck_reports([recheck])
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("verdict 必须是 站得住 或 站不住", result.stdout)
        self.assertIn("待定", result.stdout)

    def test_recheck_verdict_requires_each_field(self):
        for field in ("attempt", "evidence"):
            with self.subTest(field=field):
                self.write_reports(report("主审", self.repository), report("剃刀", self.repository))
                recheck = self.read_recheck_for_feature("alpha")
                recheck["features"][0]["verdicts"][0].pop(field)
                self.write_recheck_reports([recheck])
                self.run_check_and_assert(f"缺少字段 {field}")

    def prepare_two_required_rechecks(self):
        main, razor, root = reports_for_feature(self.repository, "root")
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
                self.write_recheck_reports([recheck_report(self.repository, feature_ids)])
                result = self.run_check(reports=self.reports_for_check())
                self.assertEqual(result.returncode, 1)
                self.assertIn("features 必须恰有一个功能对象", result.stdout)

    def test_feature_id_delete_overlaps_any_keep(self):
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        deletion = finding("删除", False, "直接做")
        deletion["target"] = "alpha"
        alpha["findings"] = [deletion]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("删除目标和 keep 行范围重叠", result.stdout)

    def test_other_feature_id_does_not_cover_this_features_files(self):
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"] = []
        change = finding("重构", True, "问你")
        change["target"] = "alpha"
        alpha["findings"] = [change]
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("未覆盖功能文件", result.stdout)
        self.assertIn("alpha/main.py:1", result.stdout)

    def test_recheck_finding_rejects_out_of_scope_targets(self):
        for target in ("beta/main.py:1", "beta"):
            with self.subTest(target=target):
                main, razor = self.recheck_scenario()
                self.write_reports(main, razor)
                recheck = self.read_recheck_for_feature("alpha")
                item = finding("重构", True, "问你")
                item["target"] = target
                recheck["features"][0]["findings"] = [item]
                self.write_recheck_reports([recheck])
                self.run_check_and_assert(
                    "复审发现目标必须在本功能", reports=self.reports_for_check(),
                )

    def test_evidence_accepts_sentence_punctuation_but_not_decimal_suffix(self):
        main, razor = self.recheck_scenario()
        self.write_reports(main, razor)
        recheck = self.read_recheck_for_feature("alpha")
        alpha = recheck["features"][0]
        alpha["verdicts"][0]["evidence"] = "结果见 README.md:1."
        self.write_recheck_reports([recheck])
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        recheck = self.read_recheck_for_feature("alpha")
        recheck["features"][0]["verdicts"][0]["evidence"] = "小数 README.md:1.5 不作为行号。"
        self.write_recheck_reports([recheck])
        result = self.run_check(reports=self.reports_for_check())
        self.assertEqual(result.returncode, 1)
        self.assertIn("evidence 必须引至少一处有效位置", result.stdout)

    def test_one_python_keep_cannot_cross_definition_boundaries(self):
        cases = (
            ("alpha/combined.py", "def first():\n    return 1\ndef second():\n    return 2\n",
             "alpha/combined.py:1-4"),
            ("alpha/conditional.py", "if flag:\n    def first():\n        return 1\ndef second():\n    return 2\n",
             "alpha/conditional.py:1-5"),
        )
        for path, source, line_range in cases:
            with self.subTest(path=path):
                self.add_tracked_file(path, source)
                main, razor, alpha = reports_for_feature(self.repository, "alpha")
                alpha["keep"].append(razor_keep_item("alpha", line_range))
                self.write_reports(main, razor)
                result = self.run_check()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(line_range, result.stdout)
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"].extend([
            razor_keep_item("alpha", "alpha/service.py:1-1"),
            razor_keep_item("alpha", "alpha/service.py:2-3"),
            razor_keep_item("alpha", "alpha/service.py:4-5"),
            razor_keep_item("alpha", "alpha/wrappers.py:1-6"),
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"].append(razor_keep_item("alpha", "alpha/front-matter.md:1-5"))
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unclosed_yaml_delimiter_does_not_hide_later_headings(self):
        self.add_tracked_file(
            "alpha/unclosed-front-matter.md",
            "---\n# First\n## Second\n",
        )
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
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
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"].append(
            razor_keep_item("alpha", "alpha/markdown-non-headings.md:1-9")
        )
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_list_marker_is_not_a_setext_underline(self):
        self.add_tracked_file("alpha/list-marker.md", "First\n-\nSecond\n---\n")
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"].append(razor_keep_item("alpha", "alpha/list-marker.md:1-4"))
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_python_parse_error_is_reported_when_checking_one_entity(self):
        self.add_tracked_file("alpha/broken.py", "def broken(:\n    return 1\n")
        main, razor, alpha = reports_for_feature(self.repository, "alpha")
        alpha["keep"].append(razor_keep_item("alpha", "alpha/broken.py:1-2"))
        self.write_reports(main, razor)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("alpha/broken.py", result.stdout)
        self.assertIn("无法检查", result.stdout)
        self.assertIn("一个实体", result.stdout)

    def test_keep_requirement_accepts_items_from_the_same_or_another_feature(self):
        for source, text in (
            ("alpha:core", "提供 alpha 功能。"),
            ("root:core", "提供 root 功能。"),
        ):
            with self.subTest(source=source):
                main, razor, alpha = reports_for_feature(self.repository, "alpha")
                alpha["keep"] = [razor_keep_item("alpha", source=source, text=text)]
                self.write_reports(main, razor)
                result = self.run_check()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_keep_requirement_rejects_missing_items_legacy_sources_and_partial_text(self):
        cases = [
            ("alpha:missing", "提供 alpha 功能。", "requirement.source"),
            ("要什么", "提供 alpha 功能。", "requirement.source"),
            ("alpha:core", "alpha 功能", "requirement.text"),
        ]
        for source, text, expected in cases:
            with self.subTest(source=source, text=text):
                main, razor, alpha = reports_for_feature(self.repository, "alpha")
                alpha["keep"] = [razor_keep_item("alpha", source=source, text=text)]
                self.write_reports(main, razor)
                result = self.run_check()
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stdout)

    def test_keep_achieved_and_cost_must_be_nonempty(self):
        for key in ("achieved", "cost"):
            with self.subTest(key=key):
                main, razor, alpha = reports_for_feature(self.repository, "alpha")
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

    def test_todo_file_coverage_in_alpha_and_root_scopes(self):
        cases = (
            ("alpha-only", {"alpha/TODO.md": "待办事项。\n"},
             {"alpha": [todo_item("alpha/TODO.md:1-1")]}),
            ("root-and-alpha", {
                "TODO.md": "仓库约定。\n", "alpha/TODO.md": "Alpha 约定。\n",
            }, {
                "root": [todo_item("TODO.md:1-1")],
                "alpha": [todo_item("alpha/TODO.md:1-1")],
            }),
        )
        for name, files, items in cases:
            with self.subTest(scope=name):
                for path, content in files.items():
                    self.add_tracked_file(path, content)
                self.write_todo_reports(items)
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
             "record", "--repo", str(self.repository), "--features", "全部",
             "--kept-markers", "无"],
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

    def _prepare_empty_items_group(self, gamma_items, gamma_directory=None,
                                   group_directory=None, direct_file=None):
        if direct_file is not None:
            self.add_tracked_file(*direct_file)
        root_path = self.repository / "repo-map" / "features.json"
        root = json.loads(root_path.read_text(encoding="utf-8"))
        root["children"].append(feature(
            "group", group_directory, children=[
                feature("gamma", gamma_directory, items=gamma_items),
            ],
        ))
        root_path.write_text(json.dumps(root, ensure_ascii=False), encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "-A"], check=True)
        feature_ids = ("group", "gamma")
        main = report("主审", self.repository, feature_ids)
        razor = report("剃刀", self.repository, feature_ids)
        if direct_file is not None and direct_file[0] == "group/TODO.md":
            for review in main["features"] + razor["features"]:
                if review["feature"] == "group":
                    review["todo_items"] = [todo_item("group/TODO.md:1-1")]
        self.write_reports(main, razor)

    def test_feature_with_direct_files_cannot_have_empty_items(self):
        self._prepare_empty_items_group(
            [], gamma_directory="gamma", direct_file=("gamma/guide.md", "Gamma guide.\n"),
        )
        result = self.run_check(scope=("group",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("条目为空，须先起草", result.stdout)
        self.assertIn("gamma", result.stdout)

    def test_fileless_parent_with_children_can_have_empty_items(self):
        item = [{"id": "core", "text": "提供 gamma 功能。", "status": "确认"}]
        self._prepare_empty_items_group(item)
        result = self.run_check(scope=("group",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_empty_unimplemented_leaf_without_files_requires_items(self):
        self._prepare_empty_items_group([])
        result = self.run_check(scope=("group",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("条目为空，须先起草", result.stdout)
        self.assertIn("gamma", result.stdout)

    def test_fileless_parent_with_direct_todo_cannot_have_empty_items(self):
        item = [{"id": "core", "text": "提供 gamma 功能。", "status": "确认"}]
        self._prepare_empty_items_group(
            item, group_directory="group", direct_file=("group/TODO.md", "待办事项。\n"),
        )
        result = self.run_check(scope=("group",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("条目为空，须先起草", result.stdout)
        self.assertIn("group", result.stdout)

    def test_a_parent_scope_expands_to_its_finest_features(self):
        root_path = self.repository / "repo-map" / "features.json"
        root = json.loads(root_path.read_text(encoding="utf-8"))
        root["children"].append(feature("group", None, children=[
            feature("gamma", None, entries=[], items=[{
                "id": "core", "text": "提供 gamma 功能。", "status": "确认",
            }]), feature("delta", None, entries=[], items=[{
                "id": "core", "text": "提供 delta 功能。", "status": "确认",
            }]),
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

    def test_direct_decision_follows_behavior_and_change_kind(self):
        valid = [
            finding("删除", False, "直接做"),
            finding("合并", False, "直接做"),
            finding("删除", True, "问你"),
            finding("重构", False, "直接做"),
            finding("改名", False, "直接做"),
            finding("挪位置", False, "直接做"),
            finding("拆分", False, "直接做"),
            finding("改清单", True, "问你"),
            finding("改清单", False, "问你"),
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
            finding("重构", False, "问你"),
            finding("改清单", False, "直接做"),
        ]
        for item in invalid:
            with self.subTest(kind=item["kind"], changes=item["changes_behavior"], decision=item["decision"]):
                main = report("主审", self.repository)
                main["features"][0]["findings"] = [item]
                self.write_reports(main, report("剃刀", self.repository))
                self.assertEqual(self.run_check().returncode, 1)

    def test_nonbehavioral_internal_changes_are_direct_unless_user_decision_is_required(self):
        self.add_tracked_file("alpha/TODO.md", "保留这条 TODO。\n")
        cases = [
            ("重构", "alpha/main.py:1", "直接做", False),
            ("重构", "alpha/main.py:1", "问你", True),
            ("删除", "alpha/TODO.md:1-1", "问你", False),
            ("孤儿", "alpha/main.py:1", "问你", False),
        ]
        for kind, target, decision, changes_behavior in cases:
            with self.subTest(kind=kind, target=target, changes_behavior=changes_behavior):
                main, razor = report("主审", self.repository), report("剃刀", self.repository)
                item = finding(kind, changes_behavior, decision)
                item["target"] = target
                main["features"][1]["findings"] = [item]
                main["features"][1]["todo_items"] = [todo_item("alpha/TODO.md:1-1")]
                razor["features"][1]["todo_items"] = [todo_item("alpha/TODO.md:1-1")]
                self.write_reports(main, razor)
                if kind == "孤儿":
                    self.add_orphan_verdict("alpha", "alpha/main.py:1-1")

                result = self.run_check()

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_accepts_an_orphan_finding_that_asks_the_user(self):
        main = report("主审", self.repository)
        main["features"][0]["findings"] = [finding("孤儿", True, "问你")]
        self.write_reports(main, report("剃刀", self.repository))
        self.add_orphan_verdict("root", "alpha/main.py:1-1")

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_orphan_findings_cannot_be_decided_directly(self):
        main = report("主审", self.repository)
        main["features"][0]["findings"] = [finding("孤儿", False, "直接做")]
        self.write_reports(main, report("剃刀", self.repository))

        result = self.run_check()

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("kind 不是允许的类型", result.stdout)
        self.assertIn("decision", result.stdout)

    def add_orphan_finding(self, feature_id, target, role="主审", keep=True):
        main = report("主审", self.repository)
        razor = report("剃刀", self.repository)
        if not keep:
            next(item for item in razor["features"]
                 if item["feature"] == feature_id)["keep"] = []
        document = main if role == "主审" else razor
        feature_review = next(
            item for item in document["features"] if item["feature"] == feature_id
        )
        orphan = finding("孤儿", False, "问你")
        orphan["target"] = target
        feature_review["findings"] = [orphan]
        self.write_reports(main, razor)

    def add_orphan_verdict(self, feature_id, lines, verdict="站得住"):
        parsed = check_reports_module._parse_line_range(
            lines, allow_single_line=True,
        )
        if parsed is not None:
            path, start, end = parsed
            lines = f"{path}:{start}-{end}"
        rechecks = self.read_recheck_reports()
        review = next(
            document for document in rechecks
            if document["features"][0]["feature"] == feature_id
        )
        review["features"][0]["verdicts"].append(recheck_verdict(
            lines,
            verdict=verdict,
            attempt=(
                f"在副本删除 {lines}，运行 `python3 -B -m unittest`，结果为退出码 0。"
            ),
        ))
        self.write_recheck_reports(rechecks)

    def test_orphan_target_requires_exactly_one_verdict(self):
        self.add_orphan_finding("alpha", "beta/main.py:1")

        result = self.run_check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("缺少孤儿目标的判定：beta/main.py:1-1", result.stdout)

    def test_recheck_report_before_orphan_finding_still_requires_orphan_verdict(self):
        self.add_orphan_finding("alpha", "beta/main.py:1")
        reports = self.reports_for_check()
        reordered_reports = (*reports[2:], *reports[:2])

        result = self.run_check(reports=reordered_reports)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("缺少孤儿目标的判定：beta/main.py:1-1", result.stdout)

    def test_orphan_target_rejects_an_extra_verdict(self):
        self.add_orphan_finding("alpha", "beta/main.py:1")
        self.add_orphan_verdict("alpha", "beta/main.py:1-1")
        self.add_orphan_verdict("alpha", "beta/main.py:1-1")

        result = self.run_check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("重复判定孤儿目标：beta/main.py:1-1", result.stdout)

    def test_orphan_verdict_lines_must_match_the_orphan_target(self):
        self.add_orphan_finding("alpha", "beta/main.py:1")
        self.add_orphan_verdict("alpha", "README.md:5-5")

        result = self.run_check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("多出不在 keep 或孤儿目标里的判定：README.md:5-5", result.stdout)
        self.assertIn("缺少孤儿目标的判定：beta/main.py:1-1", result.stdout)

    def test_orphan_only_feature_without_keep_is_still_required_to_recheck(self):
        self.add_orphan_finding("alpha", "alpha/main.py:1", role="剃刀", keep=False)

        result = self.run_check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("缺剃刀复审：alpha", result.stdout)

    def test_orphan_nonstanding_test_target_needs_no_overlapping_finding(self):
        self.add_tracked_file("tests/test_orphan.py", "value = 1\n")
        self.test_candidates["unmeasured_files"].append({
            "path": "tests/test_orphan.py", "why": "这个孤儿测试文件没有运行测试运行器。",
        })
        self.write_test_candidates()
        self.add_orphan_finding("root", "tests/test_orphan.py:1", role="剃刀")
        self.add_orphan_verdict("root", "tests/test_orphan.py:1-1", "站不住")

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_orphan_nonstanding_document_target_needs_no_overlapping_finding(self):
        self.add_orphan_finding("root", "README.md:5", role="主审")
        self.add_orphan_verdict("root", "README.md:5-5", "站不住")

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_orphan_nonstanding_code_target_must_be_in_missed_ranges(self):
        self.set_measured_usage(missed=())
        self.add_orphan_finding("alpha", "beta/main.py:1")
        self.add_orphan_verdict("alpha", "beta/main.py:1-1", "站不住")

        result = self.run_check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("beta/main.py:1 未落在 usage-coverage 的 missed 范围", result.stdout)

    def test_orphan_nonstanding_unmeasured_code_target_is_rejected(self):
        self.add_orphan_finding("alpha", "beta/main.py:1")
        self.add_orphan_verdict("alpha", "beta/main.py:1-1", "站不住")

        result = self.run_check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("beta/main.py 在 usage-coverage.unmeasured 中", result.stdout)

    def test_orphan_nonstanding_code_target_fully_missed_is_allowed_without_another_finding(self):
        self.set_measured_usage()
        main = report("主审", self.repository)
        razor = self.set_missed_candidate()
        orphan = finding("孤儿", False, "问你")
        orphan["target"] = "alpha/main.py:1"
        next(item for item in main["features"] if item["feature"] == "alpha")["findings"] = [orphan]
        self.write_reports(main, razor)
        self.add_orphan_verdict("alpha", "alpha/main.py:1-1", "站不住")

        result = self.run_check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
