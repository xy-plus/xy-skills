"""Behavior tests for cleanup report validation."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS.parent / "test_data"))
sys.path.insert(0, str(TESTS))

from check_audit import (expand_scope, load_reports, main, numbered_text,
                         validate_reports)
from audit_entities import collect_entities
from test_support import (finding, model, node_entry, report, report_input,
                          valid_reports)
from create_fixture import create_fixture_repo
from repo_map_lib.git_repository import (discover_subrepositories, gitlink_paths,
                                         tracked_paths)
from repo_map_lib.ownership import Owner, check_ownership


REPO = "/repo"
SCOPE = ["sample.alpha", "sample.beta"]
OWNERS = {
    "src/alpha.py": Owner("sample.alpha", "code"),
    "src/beta.py": Owner("sample.beta", "code"),
}


def validate(reports, scope, owners, graph):
    report_data = valid_reports(OWNERS) if reports is None else reports
    scope_values = SCOPE if scope is None else scope
    owner_data = OWNERS if owners is None else owners
    graph_data = model() if graph is None else graph
    expected_entities = {
        node: sorted(path for path, owner in owner_data.items() if owner.element == node)
        for node in graph_data["elements"]
    }
    scope_data, scope_problems = expand_scope(scope_values, graph_data)
    return scope_problems + validate_reports(
        REPO, scope_data, report_data, graph_data, owner_data, expected_entities
    )


class CheckAuditTest(unittest.TestCase):
    def test_complete_reports_cover_each_scoped_node_once(self):
        self.assertEqual(validate(None, None, None, None), [])

    def test_a_node_can_be_omitted_below_an_immediate_delete(self):
        reports = valid_reports(OWNERS)
        main_report = reports[0]["data"]
        main_report["nodes"] = [
            node_entry("sample.alpha", ["src/alpha.py"], [
                {**finding("删除", "直接做", False), "target": "sample.alpha"}
            ], "核对了删除目标"),
            node_entry("sample.beta", ["src/beta.py"], [], "核对了 beta"),
        ]
        graph = model()
        graph["elements"]["sample.alpha.child"] = {
            "id": "sample.alpha.child", "kind": "node", "title": "Child"
        }
        scope = [*SCOPE, "sample.alpha.child"]
        for item in reports[1:]:
            item["data"]["nodes"].append(node_entry(
                "sample.alpha.child",
                ["src/child.py"] if item["data"]["role"] == "剃刀" else None,
                [], "核对了 child"
            ))
        owners = {**OWNERS, "src/child.py": Owner("sample.alpha.child", "code")}
        errors = validate(reports, scope, owners, graph)
        self.assertEqual(errors, [])

    def test_direct_deletion_does_not_hide_descendant_from_other_role(self):
        reports = valid_reports(OWNERS)
        reports[0]["data"]["nodes"] = [
            node_entry("sample.alpha", ["src/alpha.py"], [
                {**finding("删除", "直接做", False), "target": "sample.alpha"}
            ], "核对了删除目标"),
            node_entry("sample.beta", ["src/beta.py"], [], "核对了 beta"),
        ]
        graph = model()
        graph["elements"]["sample.alpha.child"] = {
            "id": "sample.alpha.child", "kind": "node", "title": "Child"
        }
        owners = {**OWNERS, "src/child.py": Owner("sample.alpha.child", "code")}
        scope = [*SCOPE, "sample.alpha.child"]
        reports[2]["data"]["nodes"].append(node_entry(
            "sample.alpha.child", None, [], "核对了 child"
        ))
        errors = validate(reports, scope, owners, graph)
        self.assertTrue(any("sample.alpha.child" in error and "剃刀" in error for error in errors), errors)

    def test_requires_one_whole_graph_report(self):
        reports = valid_reports(OWNERS)
        errors = validate(reports[:2], None, None, None)
        self.assertTrue(any("全图" in error for error in errors), errors)
        reports.append(reports[2])
        self.assertTrue(any("恰好一份" in error for error in validate(reports, None, None, None)))

    def test_reports_must_match_requested_repository_and_scope(self):
        reports = valid_reports(OWNERS)
        reports[0]["data"]["repo"] = "/other"
        errors = validate(reports, None, None, None)
        self.assertTrue(any("仓路径" in error for error in errors), errors)
        reports = valid_reports(OWNERS)
        reports[0]["data"]["nodes"][0]["node"] = "sample.missing"
        self.assertTrue(any("范围外" in error or "不存在" in error
                            for error in validate(reports, None, None, None)))

    def test_scope_requires_known_unique_nodes(self):
        self.assertTrue(validate(None, ["sample.alpha", "sample.alpha"], None, None))
        self.assertTrue(validate(None, ["sample.unknown"], None, None))
        self.assertTrue(validate(None, ["全部", "sample.alpha"], None, None))

    def test_files_must_equal_the_ownership_result(self):
        reports = valid_reports(OWNERS)
        reports[0]["data"]["nodes"][0]["files"] = []
        errors = validate(reports, None, None, None)
        self.assertTrue(any("files" in error for error in errors), errors)

    def test_entity_manifest_reports_missing_extra_and_duplicate_ids(self):
        reports = valid_reports(OWNERS)
        reports[0]["data"]["nodes"][0]["entities"] = []
        errors = validate(reports, None, None, None)
        self.assertTrue(any("缺少实体 src/alpha.py" in error for error in errors), errors)

        reports = valid_reports(OWNERS)
        reports[0]["data"]["nodes"][0]["entities"].append({
            "entity": "src/not-owned.py", "verdict": "无问题", "reason": "核对了代码"
        })
        errors = validate(reports, None, None, None)
        self.assertTrue(any("多余实体 src/not-owned.py" in error for error in errors), errors)

        reports = valid_reports(OWNERS)
        reports[0]["data"]["nodes"][0]["entities"].append(
            dict(reports[0]["data"]["nodes"][0]["entities"][0])
        )
        errors = validate(reports, None, None, None)
        self.assertTrue(any("实体 src/alpha.py 重复" in error for error in errors), errors)

    def test_primary_reports_must_include_an_entities_array(self):
        reports = valid_reports(OWNERS)
        del reports[0]["data"]["nodes"][0]["entities"]
        errors = validate(reports, None, None, None)
        self.assertTrue(any("节点缺少字段：entities" in error for error in errors), errors)

    def test_entity_verdict_reason_and_finding_consistency_are_checked(self):
        reports = valid_reports(OWNERS)
        entity = reports[0]["data"]["nodes"][0]["entities"][0]
        entity["verdict"] = "未检查"
        entity["reason"] = "  "
        errors = validate(reports, None, None, None)
        self.assertTrue(any("verdict" in error for error in errors), errors)
        self.assertTrue(any("reason" in error for error in errors), errors)

        reports = valid_reports(OWNERS)
        reports[0]["data"]["nodes"][0]["entities"][0]["verdict"] = "见发现"
        errors = validate(reports, None, None, None)
        self.assertTrue(any("见发现" in error and "findings" in error for error in errors), errors)

    def test_entity_objects_require_exact_fields_and_nonempty_ids(self):
        reports = valid_reports(OWNERS)
        reports[0]["data"]["nodes"][0]["entities"][0].pop("reason")
        errors = validate(reports, None, None, None)
        self.assertTrue(any("entities[0] 缺少字段：reason" in error for error in errors), errors)

        reports = valid_reports(OWNERS)
        reports[0]["data"]["nodes"][0]["entities"][0]["entity"] = " "
        errors = validate(reports, None, None, None)
        self.assertTrue(any("entity 必须是非空字符串" in error for error in errors), errors)

    def test_whole_graph_nodes_must_not_list_files_or_entities(self):
        reports = valid_reports(OWNERS)
        reports[2]["data"]["nodes"][0]["entities"] = []
        reports[2]["data"]["nodes"][0]["files"] = []
        errors = validate(reports, None, None, None)
        self.assertTrue(any("全图" in error and "entities" in error for error in errors), errors)
        self.assertTrue(any("全图" in error and "files" in error for error in errors), errors)

    def test_report_and_node_fields_are_strict(self):
        reports = valid_reports(OWNERS)
        del reports[0]["data"]["nodes"][0]["checked"]
        errors = validate(reports, None, None, None)
        self.assertTrue(any("checked" in error for error in errors), errors)
        reports = valid_reports(OWNERS)
        reports[0]["data"]["extra"] = "unexpected"
        self.assertTrue(any("多余字段" in error
                            for error in validate(reports, None, None, None)))

    def test_empty_findings_need_a_nonblank_checked_description(self):
        reports = valid_reports(OWNERS)
        reports[0]["data"]["nodes"][0]["checked"] = " \n"
        self.assertTrue(any("checked" in error for error in validate(reports, None, None, None)))

    def test_finding_enum_and_required_fields_are_checked(self):
        reports = valid_reports(OWNERS)
        entry = reports[0]["data"]["nodes"][0]
        entry["findings"] = [finding("重构", "问你", True)]
        entry["findings"][0]["kind"] = "未知"
        self.assertTrue(any("kind" in error for error in validate(reports, None, None, None)))
        reports = valid_reports(OWNERS)
        entry = reports[0]["data"]["nodes"][0]
        entry["findings"] = [finding("重构", "问你", True)]
        del entry["findings"][0]["benefit"]
        self.assertTrue(any("benefit" in error for error in validate(reports, None, None, None)))

    def test_direct_action_is_limited_to_behavior_preserving_delete_or_merge(self):
        cases = [
            finding("删除", "问你", False),
            finding("合并", "直接做", True),
            finding("重构", "直接做", False),
        ]
        for item in cases:
            with self.subTest(item=item):
                reports = valid_reports(OWNERS)
                reports[0]["data"]["nodes"][0]["findings"] = [item]
                self.assertTrue(validate(reports, None, None, None))

    def test_graph_changes_must_be_asked(self):
        reports = valid_reports(OWNERS)
        item = finding("改图", "直接做", False)
        reports[0]["data"]["nodes"][0]["findings"] = [item]
        self.assertTrue(any("改图" in error and "问你" in error
                            for error in validate(reports, None, None, None)))

    def test_basis_text_must_come_from_the_named_rule(self):
        reports = valid_reports(OWNERS)
        item = finding("重构", "问你", True)
        rule = numbered_text(TESTS.parents[1] / "xy-iron-law" / "SKILL.md", 1)
        item["basis"]["text"] = rule.replace("不改、在别处", "不改，在别处")
        reports[0]["data"]["nodes"][0]["findings"] = [item]
        errors = validate(reports, None, None, None)
        self.assertTrue(any("basis.text" in error for error in errors), errors)
        self.assertTrue(any(rule in error for error in errors), errors)
        item["basis"]["text"] = "第一性原理"
        item["basis"]["source"] = "xy-repo-rules 第 1 条"
        self.assertTrue(any("basis.text" in error for error in validate(reports, None, None, None)))

    def test_numbered_rules_read_each_skill_file_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill_path = Path(temporary) / "rules.md"
            skill_path.write_text("1. 第一条\n正文。\n2. 第二条\n结束。\n", encoding="utf-8")
            original_read_text = Path.read_text
            reads = []

            def count_rule_read(path, *args, **kwargs):
                if path == skill_path:
                    reads.append(path)
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", count_rule_read):
                numbered_text(skill_path, 1)
                numbered_text(skill_path, 2)
            self.assertEqual(reads, [skill_path])

    def test_invalid_delete_finding_cannot_hide_descendants(self):
        reports = valid_reports(OWNERS)
        graph = model()
        graph["elements"]["sample.alpha.child"] = {
            "id": "sample.alpha.child", "kind": "node", "title": "Child"
        }
        owners = {**OWNERS, "src/child.py": Owner("sample.alpha.child", "code")}
        for item in reports[1:]:
            item["data"]["nodes"].append(node_entry(
                "sample.alpha.child",
                ["src/child.py"] if item["data"]["role"] == "剃刀" else None,
                [], "核对了 child"
            ))
        main_nodes = reports[0]["data"]["nodes"]
        main_nodes[0]["findings"] = [{
            **finding("删除", "直接做", False),
            "target": "sample.alpha",
            "basis": {"source": "xy-iron-law 第 1 条", "text": "不存在的依据"},
        }]
        errors = validate(reports, [*SCOPE, "sample.alpha.child"], owners, graph)
        self.assertTrue(any("sample.alpha.child" in error and "主审" in error for error in errors))

    def test_delete_finding_on_another_node_cannot_hide_an_ancestor_child(self):
        reports = valid_reports(OWNERS)
        graph = model()
        graph["elements"]["sample.alpha.child"] = {
            "id": "sample.alpha.child", "kind": "node", "title": "Child"
        }
        owners = {**OWNERS, "src/child.py": Owner("sample.alpha.child", "code")}
        reports[0]["data"]["nodes"][1]["findings"] = [{
            **finding("删除", "直接做", False),
            "target": "sample.alpha",
        }]
        for item in reports[1:]:
            item["data"]["nodes"].append(node_entry(
                "sample.alpha.child",
                ["src/child.py"] if item["data"]["role"] == "剃刀" else None,
                [], "核对了 child"
            ))
        errors = validate(reports, [*SCOPE, "sample.alpha.child"], owners, graph)
        self.assertTrue(any("sample.alpha.child" in error and "主审" in error for error in errors))

    def test_unhashable_enum_values_are_reported_without_crashing(self):
        reports = valid_reports(OWNERS)
        item = finding("重构", "问你", True)
        item["kind"] = ["重构"]
        reports[0]["data"]["nodes"][0]["findings"] = [item]
        errors = validate(reports, None, None, None)
        self.assertTrue(any("kind" in error for error in errors), errors)

    def test_duplicate_node_across_reports_is_rejected(self):
        reports = valid_reports(OWNERS)
        reports.append(report_input("second-main.json", report(
            "主审", [node_entry("sample.alpha", ["src/alpha.py"], [], "已核对")], REPO
        )))
        errors = validate(reports, None, None, None)
        self.assertTrue(any("sample.alpha" in error and "恰好出现一次" in error for error in errors))

    def test_cli_rejects_missing_explicit_parameters(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(main(["check_audit.py"]), 2)

    def test_cli_checks_reports_and_file_ownership_in_a_real_fixture_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = create_fixture_repo(Path(temporary) / "review-target")
            graph = {
                "elements": {
                    "cleanup_fixture": {"links": [
                        {"url": "../README.md", "title": "docs"}
                    ]},
                    "cleanup_fixture.core": {"links": [
                        {"url": "../src/cleanup_fixture/", "title": "code"}
                    ]},
                    "cleanup_fixture.cli": {"links": [
                        {"url": "../src/cleanup_fixture/cli.py", "title": "code"}
                    ]},
                    "cleanup_fixture.tests": {"links": [
                        {"url": "../tests/", "title": "tests"}
                    ]},
                },
                "relations": {},
            }
            tracked = tracked_paths(repository)
            ownership_problems, owners = check_ownership(
                repository, graph, tracked, gitlink_paths(repository),
                discover_subrepositories(repository)
            )
            self.assertEqual(ownership_problems, [])
            node_names = sorted(graph["elements"])
            report_paths = []
            expected_entities = collect_entities(repository, owners, node_names)
            for role in ("主审", "剃刀", "全图"):
                nodes = []
                for node in node_names:
                    files = sorted(path for path, owner in owners.items()
                                   if owner.element == node)
                    entry = node_entry(
                        node, files if role != "全图" else None, [], "核对过节点文件"
                    )
                    if role != "全图":
                        entry["entities"] = [
                            {"entity": entity, "verdict": "无问题",
                             "reason": "核对了入口和实现"}
                            for entity in expected_entities[node]
                        ]
                    nodes.append(entry)
                document = report(role, nodes, str(repository))
                path = repository / f"{role}.json"
                path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
                report_paths.append(str(path))
            arguments = ["check", "--repo", str(repository), "--scope", "全部",
                         "--reports", *report_paths]
            with patch("check_audit.check_repository", return_value=([], graph, owners)) as graph_check, \
                    patch("check_audit.expand_scope", wraps=expand_scope) as expand:
                self.assertEqual(main(arguments), 0)
            graph_check.assert_called_once_with(repository, check_approval=False)
            self.assertEqual(expand.call_count, 1)

    def test_load_reports_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text('{"role":"主审","role":"全图"}', encoding="utf-8")
            reports, problems = load_reports([path])
        self.assertEqual(reports, [])
        self.assertTrue(any("重复键：role" in problem for problem in problems), problems)

    def test_entity_finding_verdict_reason_must_match_a_finding_target(self):
        reports = valid_reports(OWNERS)
        node = reports[0]["data"]["nodes"][0]
        node["findings"] = [finding("重构", "问你", True)]
        node["entities"][0]["verdict"] = "见发现"
        node["entities"][0]["reason"] = "src/other.py:7"
        errors = validate(reports, None, None, None)
        self.assertTrue(any("reason" in error and "target" in error for error in errors), errors)
        node["entities"][0]["reason"] = node["findings"][0]["target"]
        errors = validate(reports, None, None, None)
        self.assertFalse(any("reason" in error and "target" in error for error in errors), errors)

    def test_repository_context_reuses_owners_returned_by_repository_check(self):
        from check_audit import repository_context

        with tempfile.TemporaryDirectory() as temporary:
            repository = create_fixture_repo(Path(temporary) / "review-target")
            graph = {"elements": {"fixture": {}}, "relations": {}}
            owners = {"src/cleanup_fixture/core.py": object()}
            with patch("check_audit.check_repository", return_value=([], graph, owners)), \
                    patch("repo_map_lib.checker.check_ownership") as ownership_check:
                root, actual_graph, tracked, actual_owners = repository_context(repository)
            self.assertEqual(root, repository)
            self.assertIs(actual_graph, graph)
            self.assertIn("src/cleanup_fixture/core.py", tracked)
            self.assertIs(actual_owners, owners)
            ownership_check.assert_not_called()

    def test_entities_command_prints_the_selected_node_manifest_as_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = create_fixture_repo(Path(temporary) / "review-target")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "entities", "--repo", str(repository), "--scope",
                    "cleanup_fixture.core"
                ])
            self.assertEqual(status, 0)
            document = json.loads(output.getvalue())
            core_entities = document["nodes"]["cleanup_fixture.core"]
            self.assertIn("src/cleanup_fixture/core.py", core_entities)
            self.assertIn("src/cleanup_fixture/core.py::parse_external_result", core_entities)
            self.assertEqual(set(document["nodes"]), {"cleanup_fixture.core"})


if __name__ == "__main__":
    unittest.main()
