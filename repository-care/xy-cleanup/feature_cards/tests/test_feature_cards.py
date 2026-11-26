import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
REPOSITORY_CARE = ROOT / "repository-care"
XY_CLEANUP_DIRECTORY = REPOSITORY_CARE / "xy-cleanup"
sys.path.insert(0, str(REPOSITORY_CARE / "tests"))
sys.path.insert(0, str(XY_CLEANUP_DIRECTORY / "feature_cards"))
sys.path.insert(0, str(XY_CLEANUP_DIRECTORY / "decision_page"))

from map_fixture import make_cleanup_repo  # noqa: E402
import decision_page  # noqa: E402
import feature_cards  # noqa: E402


COMMAND = XY_CLEANUP_DIRECTORY / "feature_cards" / "feature_cards.py"


def card(feature_id, **changes):
    value = {
        "feature": feature_id,
        "used": [
            {
                "who": f"现在有人用 {feature_id}。",
                "evidence": f"README 实际使用记录提到 {feature_id}。",
            }
        ],
        "unused": [],
        "changed": [],
        "todo": [],
        "map_issues": [],
        "suggestion": [],
        "recommended": "留",
    }
    value.update(changes)
    return value


def unused_card(**changes):
    item = {
        "what": "旧版入口。", "evidence": "README 没有列它。",
        "reverse_search": "检查本仓全部分支、tag、发布产物和说明，没有其他引用。",
        "lines": 3, "risk": "脚本可能仍在调用。",
    }
    item.update(changes)
    return card("alpha", used=[], unused=[item])


class FeatureCardsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.primary = make_cleanup_repo(self)
        subprocess.run(
            ["git", "-C", str(self.primary), "config", "user.name", "Test"], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.primary),
                "config",
                "user.email",
                "test@example.com",
            ],
            check=True,
        )
        self._commit_repository_at(self.primary, "初始提交")
        self.repository = self.directory / "linked-repository"
        subprocess.run(
            [
                "git",
                "-C",
                str(self.primary),
                "worktree",
                "add",
                "--detach",
                str(self.repository),
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.output = self.directory / "decisions.json"

    def write_cards(self, cards):
        paths = []
        for index, value in enumerate(cards):
            path = self.directory / f"card-{index}.json"
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            paths.append(path)
        return paths

    def build_card_command(self, scope=("全部",), review="全部"):
        return [
            sys.executable,
            "-B",
            str(COMMAND),
            "--repo",
            str(self.repository),
            "--scope",
            *scope,
            "--review",
            review,
        ]

    def run_cards(self, cards, scope=("全部",), review="全部", add_args=()):
        arguments = self.build_card_command(scope, review)
        paths = self.write_cards(cards)
        return self.run_card_paths(paths, arguments, add_args)

    def run_card_paths(self, paths, arguments, add_args=()):
        arguments.extend(("--cards", *(str(path) for path in paths)))
        arguments.extend(("--output", str(self.output), *add_args))
        return subprocess.run(arguments, capture_output=True, text=True, check=False)

    def default_cards(self):
        return [card("root"), card("alpha"), card("beta")]

    def first_decision_item(self):
        decisions = json.loads(self.output.read_text(encoding="utf-8"))
        return decisions["groups"][0]["items"][0]

    def test_cards_use_the_shared_json_shape_checker(self):
        with patch.object(
            feature_cards,
            "check_value",
            wraps=feature_cards.check_value,
        ) as shared_check:
            problems = feature_cards._card_errors(card("alpha"), "卡片")

        self.assertEqual(problems, [])
        self.assertTrue(shared_check.called)

    def test_rejects_missing_or_empty_reverse_search(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                value = unused_card()
                if missing:
                    value["unused"][0].pop("reverse_search")
                else:
                    value["unused"][0]["reverse_search"] = ""

                result = self.run_cards([value], scope=("alpha",), review="全部")

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                expected = "缺少字段" if missing else "不能为空"
                self.assertIn(expected, result.stdout + result.stderr)

    def test_missing_registered_repository_blocks_only_unused_decisions(self):
        self._set_used_by_repositories(["../missing-reverse-source"])

        result = self.run_cards([unused_card()], scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("反查不完整，不能判没人用", output)
        self.assertIn("missing-reverse-source", output)

        result = self.run_cards([card("alpha")], scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_registered_repository_paths_resolve_from_primary_checkout(self):
        reverse_source = self.directory / "reverse-source"
        self._make_git_repository(reverse_source)
        self._set_used_by_repositories([os.path.relpath(reverse_source, self.primary)])
        deep_checkout = self.directory / "isolated" / "nested" / "worktree"
        deep_checkout.parent.mkdir(parents=True)
        worktree_args = [
            "git", "-C", str(self.repository), "worktree", "add", "--detach",
            str(deep_checkout), "HEAD",
        ]
        subprocess.run(worktree_args, check=True, capture_output=True, text=True)
        self.repository = deep_checkout
        self.assertEqual(feature_cards.primary_checkout_path(self.repository), self.primary)

        result = self.run_cards(
            [unused_card(reverse_search="检查登记仓，没有产品代码引用。")],
            scope=("alpha",),
            review="全部",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_used_by_repositories_blocks_unused_decisions(self):
        cards = self.default_cards()
        cards[1]["unused"] = [
            {
                "what": "可选的旧格式导出入口。",
                "evidence": "README 实际使用记录只选了新版入口。",
                "reverse_search": "本仓全部分支、tag 和发布产物均没有其他引用。",
                "lines": 12,
                "risk": "旧调用方可能需要迁移。",
            }
        ]
        cards[1]["suggestion"] = ["删除旧格式导出入口。"]
        cards[1]["recommended"] = "照建议"

        result = self.run_cards(cards)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout + result.stderr,
            "根功能还没登记 used_by_repositories，反查范围没定，不能判没人用；"
            "先按清扫第 2 步问用户，按回答写进去（不查别的仓写 []）。\n",
        )

    def test_empty_used_by_repositories_allows_unused_decisions(self):
        self._set_used_by_repositories([])

        result = self.run_cards(
            [unused_card()],
            scope=("alpha",),
            review="全部",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        decisions = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertIs(decision_page.validate_decisions(decisions), decisions)

    def test_three_level_feature_title_lists_each_ancestor_from_root(self):
        self._add_alpha_child_feature(self.repository)
        self._commit_repository("添加 Alpha 子功能")

        result = self.run_cards(
            [card("root"), card("alpha"), card("alpha-child"), card("beta")]
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        items = json.loads(self.output.read_text(encoding="utf-8"))["groups"][0][
            "items"
        ]
        child = next(item for item in items if item["id"] == "alpha-child")
        self.assertEqual(child["title"], "Root › Alpha › Alpha 子功能")
        root = next(item for item in items if item["id"] == "root")
        self.assertEqual(root["title"], "Root")

    def test_scoped_feature_title_still_starts_at_the_map_root(self):
        self._add_alpha_child_feature(self.repository)
        self._commit_repository("添加 Alpha 子功能")

        result = self.run_cards(
            [card("alpha-child")], scope=("alpha-child",), review="全部"
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = json.loads(self.output.read_text(encoding="utf-8"))["groups"][0][
            "items"
        ][0]
        self.assertEqual(item["title"], "Root › Alpha › Alpha 子功能")

    def test_front_lines_put_what_usage_and_actions_in_order(self):
        value = card(
            "alpha",
            used=[
                {"who": "值班人员用 alpha 看状态。", "evidence": "查值班配置。"},
                {"who": "维护者用 alpha 改配置。", "evidence": "查维护脚本。"},
            ],
            unused=[
                {
                    "what": "旧版导出按钮。",
                    "evidence": "README 未提到。",
                "reverse_search": "检查本仓各分支、tag 和发布产物，没有其他引用。",
                    "lines": 1,
                    "risk": "旧调用方可能还在用。",
                }
            ],
            suggestion=["删除旧 alpha 入口。"],
            map_issues=["把 alpha 的功能说明改成人话。"],
            recommended="照建议",
        )

        result = self.run_cards([value], scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = json.loads(self.output.read_text(encoding="utf-8"))["groups"][0][
            "items"
        ][0]
        self.assertEqual(
            item["lines"],
            [
                "要什么\t提供 alpha 功能。",
                "谁在用\t值班人员用 alpha 看状态；维护者用 alpha 改配置。没人用：旧版导出按钮。",
                "照建议会做\t删除旧 alpha 入口；改图上文字 1 处，见细节。",
            ],
        )
        self.assertEqual(
            [line for line in item["lines"] if line.startswith("谁在用\t")],
            ["谁在用\t值班人员用 alpha 看状态；维护者用 alpha 改配置。没人用：旧版导出按钮。"],
        )
        self.assertFalse(any(line.startswith("没人用") for line in item["lines"]))

    def test_multiple_used_who_are_joined_with_mixed_periods(self):
        value = card(
            "alpha",
            used=[
                {"who": "值班人员看 alpha 状态。", "evidence": "查值班配置。"},
                {"who": "维护者改 alpha 配置", "evidence": "查维护脚本。"},
            ],
        )

        result = self.run_cards([value], scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.first_decision_item()["lines"][1],
            "谁在用\t值班人员看 alpha 状态；维护者改 alpha 配置。",
        )

    def test_multiple_unused_descriptions_are_joined_once(self):
        value = card(
            "alpha",
            used=[],
            unused=[
                {
                    "what": "旧版导出按钮。",
                    "evidence": "README 未提到。",
                    "reverse_search": "检查本仓各分支、tag 和说明，没有其他引用。",
                    "lines": 1,
                    "risk": "旧调用方可能还在用。",
                },
                {
                    "what": "调试命令",
                    "evidence": "仓内没有调用。",
                    "reverse_search": "检查本仓全部分支、tag、发布产物和说明，没有其他引用。",
                    "lines": 2,
                    "risk": "维护者可能手工使用。",
                },
            ],
        )

        result = self.run_cards([value], scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.first_decision_item()["lines"],
            [
                "要什么\t提供 alpha 功能。",
                "谁在用\t没人用：旧版导出按钮；调试命令。",
            ],
        )

    def test_multiple_suggestions_are_joined_with_mixed_periods(self):
        value = card(
            "alpha",
            suggestion=["删除旧 alpha 入口。", "保留新版入口"],
            recommended="照建议",
        )

        result = self.run_cards([value], scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.first_decision_item()["lines"][-1],
            "照建议会做\t删除旧 alpha 入口；保留新版入口。",
        )

    def test_only_map_issues_produce_one_summarized_action_line(self):
        value = card(
            "alpha",
            map_issues=["改 alpha 的功能说明。", "补上 alpha 的条目。"],
            recommended="照建议",
        )

        result = self.run_cards([value], scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        action_lines = [
            line
            for line in self.first_decision_item()["lines"]
            if line.startswith("照建议会做\t")
        ]
        self.assertEqual(action_lines, ["照建议会做\t改图上文字 2 处，见细节。"])

    def test_approved_changed_what_shows_its_previous_wording(self):
        self._commit_repository("初始提交")
        self._record_and_commit("全部", "认可全图")
        manifest = self.repository / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["children"][0]["what"] = "现在的 alpha 功能说明。"
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self._commit_repository("改写 alpha 说明")

        result = self.run_cards([card("alpha")], scope=("alpha",), review="变过的")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = self.first_decision_item()
        self.assertEqual(
            item["lines"][:2],
            ["要什么\t现在的 alpha 功能说明。", "原来\t提供 alpha 功能。"],
        )

    def test_approved_draft_items_are_listed_after_front_lines_with_previous_wording(self):
        manifest = self.repository / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        alpha = document["children"][0]
        alpha["items"].append(
            {"id": "extra", "text": "提供 alpha 附加能力。", "status": "确认"}
        )
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self._commit_repository("初始提交")
        self._record_and_commit("全部", "认可全图")
        document = json.loads(manifest.read_text(encoding="utf-8"))
        alpha = document["children"][0]
        alpha["items"][0]["text"] = "提供新版 alpha 功能。"
        alpha["items"][0]["status"] = "起草"
        alpha["items"][1]["status"] = "起草"
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self._commit_repository("更新 alpha 草稿")

        value = card(
            "alpha",
            suggestion=["删除旧 alpha 入口。"],
            recommended="照建议",
        )
        result = self.run_cards([value], scope=("alpha",), review="变过的")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = self.first_decision_item()
        self.assertEqual(
            item["lines"],
            [
                "要什么\t提供 alpha 功能。",
                "谁在用\t现在有人用 alpha。",
                "照建议会做\t删除旧 alpha 入口。",
                "起草条目 core\t提供新版 alpha 功能。（原来：提供 alpha 功能。）",
                "起草条目 extra\t提供 alpha 附加能力。",
            ],
        )
        self.assertEqual(item["details"]["title"], "细节：理由、依据 1 条")
        self.assertNotIn("起草条目 core\t提供新版 alpha 功能。", item["details"]["lines"])
        self.assertNotIn("起草条目 extra\t提供 alpha 附加能力。", item["details"]["lines"])

    def test_approved_draft_reason_and_changes_are_in_the_details(self):
        self._commit_repository("初始提交")
        self._record_and_commit("全部", "认可全图")
        manifest = self.repository / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["children"][0]["why"] = {
            "text": "alpha 让用户更快完成工作。",
            "source": "起草",
            "quote": "",
            "from": "",
        }
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self._commit_repository("改写 alpha 理由")

        result = self.run_cards(
            [card("alpha", changed=["改了 alpha 的实现。"])],
            scope=("alpha",),
            review="变过的",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        details = self.first_decision_item()["details"]
        self.assertEqual(
            details,
            {
                "title": "细节：理由（等你拍板）、变了什么 1 条、依据 1 条",
                "lines": [
                    "为什么\talpha 让用户更快完成工作。（起草，等你拍板）",
                    "原来\t让功能容易使用。",
                    "变了什么\t改了 alpha 的实现。",
                    "在用的依据\tREADME 实际使用记录提到 alpha。",
                ],
            },
        )

    def test_unapproved_draft_items_are_folded_in_the_details(self):
        manifest = self.repository / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        alpha = document["children"][0]
        alpha["why"] = {
            "text": "让 alpha 易于使用。",
            "source": "起草",
            "quote": "",
            "from": "",
        }
        alpha["items"] = [
            {"id": "core", "text": "提供 alpha 核心能力。", "status": "起草"},
            {"id": "extra", "text": "提供 alpha 附加能力。", "status": "起草"},
        ]
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self._commit_repository("起草 alpha 条目")

        result = self.run_cards(
            [card("alpha")], scope=("alpha",), review="全部"
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = json.loads(self.output.read_text(encoding="utf-8"))["groups"][0][
            "items"
        ][0]
        self.assertEqual(
            item["details"],
            {
                "title": "细节：理由（等你拍板）、依据 1 条、起草条目 2 条",
                "lines": [
                    "为什么\t让 alpha 易于使用。（起草，等你拍板）",
                    "在用的依据\tREADME 实际使用记录提到 alpha。",
                    "起草条目 core\t提供 alpha 核心能力。",
                    "起草条目 extra\t提供 alpha 附加能力。",
                ],
            },
        )

    def test_front_contains_what_and_usage_without_unapproved_drafts(self):
        manifest = self.repository / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["children"][0]["items"] = [
            {"id": "core", "text": "起草中的 alpha 核心能力。", "status": "起草"}
        ]
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self._commit_repository("起草 alpha 条目")

        result = self.run_cards([card("alpha")], scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = self.first_decision_item()
        self.assertEqual(
            item["lines"][:2],
            ["要什么\t提供 alpha 功能。", "谁在用\t现在有人用 alpha。"],
        )
        self.assertNotIn("起草中的 alpha 核心能力。", "\n".join(item["lines"]))

    def test_details_summarize_and_list_card_evidence(self):
        manifest = self.repository / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        alpha = document["children"][0]
        alpha["why"] = {
            "text": "让 alpha 有用。",
            "source": "起草",
            "quote": "",
            "from": "",
        }
        alpha["items"] = [
            {"id": "core", "text": "提供 alpha 核心能力。", "status": "起草"}
        ]
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self._commit_repository("起草 alpha 内容")
        value = card(
            "alpha",
            used=[{"who": "值班人员看状态。", "evidence": "查值班配置。"}],
            unused=[
                {
                    "what": "旧版导出按钮。",
                    "evidence": "查 README 和部署配置。",
                    "reverse_search": "检查本仓全部分支、tag、发布产物和说明，没有其他引用。",
                    "lines": 12,
                    "risk": "旧用户可能还在用。",
                }
            ],
            todo=["大问题还没解决。"],
            map_issues=["功能说明需要改。"],
        )

        result = self.run_cards([value], scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        details = self.first_decision_item()["details"]
        self.assertEqual(
            details,
            {
                "title": "细节：理由（等你拍板）、依据 3 条、可减 12 行、风险 1 条、图上要改 1 处、TODO 大问题 1 条、起草条目 1 条",
                "lines": [
                    "为什么\t让 alpha 有用。（起草，等你拍板）",
                    "在用的依据\t查值班配置。",
                    "没人用的依据\t查 README 和部署配置。",
                    "反查的依据\t检查本仓全部分支、tag、发布产物和说明，没有其他引用。",
                    "可减\t12 行",
                    "风险\t旧用户可能还在用。",
                    "图上要改\t功能说明需要改。",
                    "TODO 大问题\t大问题还没解决。",
                    "起草条目 core\t提供 alpha 核心能力。",
                ],
            },
        )

    def test_recommendation_groups_put_changes_before_keeps(self):
        cards = self.default_cards()
        cards[1]["suggestion"] = ["删除 alpha 旧入口。"]
        cards[1]["recommended"] = "照建议"
        cards[2]["recommended"] = "不要这个功能"

        result = self.run_cards(cards)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        groups = json.loads(self.output.read_text(encoding="utf-8"))["groups"]
        self.assertEqual(
            [
                (group["title"], [item["id"] for item in group["items"]])
                for group in groups
            ],
            [
                ("建议改或删（2 个）", ["alpha", "beta"]),
                ("建议留着不动（1 个）", ["root"]),
            ],
        )

    def test_recommendation_page_omits_an_empty_group(self):
        result = self.run_cards(self.default_cards())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        groups = json.loads(self.output.read_text(encoding="utf-8"))["groups"]
        self.assertEqual([group["title"] for group in groups], ["建议留着不动（3 个）"])

    def test_item_without_advice_has_no_follow_recommendation_option(self):
        result = self.run_cards(self.default_cards())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        items = json.loads(self.output.read_text(encoding="utf-8"))["groups"][0][
            "items"
        ]
        self.assertEqual(items[0]["options"], ["留", "按我写的改"])
        self.assertEqual(items[1]["options"], ["留", "按我写的改", "不要这个功能"])

    def test_page_top_has_four_plain_sentences(self):
        result = self.run_cards(self.default_cards())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        intro = json.loads(self.output.read_text(encoding="utf-8"))["intro"]
        self.assertEqual(
            intro,
            [
                "本页 3 个功能：全部重审 3 个。",
                "每个功能选一项；写了备注就以备注为准。",
                "选「照建议」「留」「按我写的改」都表示认可这张卡片上的「要什么」、理由和起草条目，收在细节里的也算；不对的地方写进备注。",
                "「留」不动代码、测试、文档和 TODO；「不要这个功能」连图上的登记一起删，只有没有子功能的功能能选。",
            ],
        )

    def test_page_top_counts_plan_reasons_in_first_seen_order(self):
        self._commit_repository("初始提交")
        self._record_and_commit("alpha", "认可 alpha")
        manifest = self.repository / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["children"][0]["what"] = "改过的 alpha 说明。"
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self._commit_repository("改 alpha 说明")

        result = self.run_cards(self.default_cards(), review="变过的")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        intro = json.loads(self.output.read_text(encoding="utf-8"))["intro"]
        self.assertEqual(
            intro[0], "本页 3 个功能：没有认可记录 2 个、认可后改过 1 个。"
        )

    def test_mixed_recommendations_explain_group_order(self):
        cards = self.default_cards()
        cards[1]["suggestion"] = ["删除 alpha 旧入口。"]
        cards[1]["recommended"] = "照建议"

        result = self.run_cards(cards)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        intro = json.loads(self.output.read_text(encoding="utf-8"))["intro"]
        self.assertTrue(intro[0].endswith("建议改或删的排在前面。"))

    def test_plan_with_no_changed_features_is_rejected(self):
        self._commit_repository("初始提交")
        self._record_and_commit("全部", "首次认可")

        result = self.run_cards([card("alpha")], review="变过的")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("范围内没有变过的功能", result.stdout + result.stderr)
        self.assertFalse(self.output.exists())

    def test_parent_cannot_recommend_removing_its_children(self):
        cards = self.default_cards()
        cards[0]["recommended"] = "不要这个功能"

        result = self.run_cards(cards)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("root", result.stdout + result.stderr)
        self.assertFalse(self.output.exists())

    def test_leaf_can_recommend_removal(self):
        cards = self.default_cards()
        cards[2]["recommended"] = "不要这个功能"

        result = self.run_cards(cards)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        decisions = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(
            decisions["groups"][0]["items"][0]["recommended"],
            "不要这个功能",
        )

    def test_rejects_missing_extra_duplicate_and_unknown_cards(self):
        cases = (
            (self.default_cards()[:-1], ("全部",), "缺少卡片"),
            (
                [card("root"), card("alpha"), card("beta"), card("beta")],
                ("全部",),
                "重复卡片",
            ),
            ([card("alpha"), card("beta")], ("alpha",), "多出的卡片"),
            ([card("alpha"), card("missing")], ("alpha",), "未知功能"),
        )
        for cards, scope, label in cases:
            with self.subTest(label=label):
                self.output.unlink(missing_ok=True)
                result = self.run_cards(cards, scope=scope)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse(self.output.exists())

    def test_used_entries_require_nonempty_who_and_evidence(self):
        cards = self.default_cards()
        cards[1]["used"][0]["evidence"] = "  "

        result = self.run_cards(cards)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("卡片[1].used[0].evidence", result.stdout + result.stderr)

    def test_used_is_limited_to_three_entries(self):
        cards = self.default_cards()
        cards[1]["used"] = [
            {"who": f"使用者 {index}。", "evidence": f"依据 {index}。"}
            for index in range(4)
        ]

        result = self.run_cards(cards)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("卡片[1].used 最多 3 条", result.stdout + result.stderr)

    def test_unapproved_feature_must_not_list_changes_since_approval(self):
        cards = [card("alpha", changed=["alpha 有改动。"])]

        result = self.run_cards(cards, scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "alpha 没有认可记录，changed 必须为 []",
            result.stdout + result.stderr,
        )

    def test_map_issue_alone_is_enough_for_follow_recommendation(self):
        cards = [
            card(
                "alpha",
                map_issues=["把 alpha 的功能说明改成人话。"],
                recommended="照建议",
            )
        ]

        result = self.run_cards(cards, scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = json.loads(self.output.read_text(encoding="utf-8"))["groups"][0][
            "items"
        ][0]
        self.assertIn("照建议", item["options"])

    def test_follow_recommendation_requires_some_advice(self):
        cards = [card("alpha", recommended="照建议")]

        result = self.run_cards(cards, scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "卡片[0].recommended 为 照建议 时 suggestion 和 map_issues 不能都空",
            result.stdout + result.stderr,
        )

    def test_rejects_malformed_card_fields(self):
        mutations = (
            lambda value: value.pop("todo"),
            lambda value: value.update(extra="不允许"),
            lambda value: value["used"][0].update(who="  "),
            lambda value: value["unused"].append(
                {
                    "what": "不用的入口。",
                    "evidence": "没有调用。",
                    "reverse_search": "检查本仓全部分支和 tag。",
                    "lines": 1,
                }
            ),
            lambda value: value["unused"].append(
                {
                    "what": "不用的入口。",
                    "evidence": "没有调用。",
                    "reverse_search": "检查本仓全部分支和 tag。",
                    "lines": 1,
                    "risk": "无。",
                    "extra": True,
                }
            ),
            lambda value: value["unused"].append(
                {
                    "what": " ",
                    "evidence": "没有调用。",
                    "reverse_search": "检查本仓全部分支和 tag。",
                    "lines": 1,
                    "risk": "无。",
                }
            ),
            lambda value: value["unused"].append(
                {
                    "what": "不用的入口。",
                    "evidence": "没有调用。",
                    "reverse_search": "检查本仓全部分支和 tag。",
                    "lines": -1,
                    "risk": "无。",
                }
            ),
            lambda value: value["unused"].append(
                {
                    "what": "不用的入口。",
                    "evidence": "没有调用。",
                    "reverse_search": "检查本仓全部分支和 tag。",
                    "lines": True,
                    "risk": "无。",
                }
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.output.unlink(missing_ok=True)
                cards = self.default_cards()
                mutate(cards[1])
                result = self.run_cards(cards)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse(self.output.exists())

    def test_missing_or_duplicate_key_card_json_exits_two(self):
        arguments = self.build_card_command()
        paths = self.write_cards(self.default_cards())
        paths[0].write_text('{"feature":"root","feature":"root"}', encoding="utf-8")

        result = self.run_card_paths(paths, arguments.copy())

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(self.output.exists())

        result = self.run_card_paths(
            [self.directory / "absent.json"], arguments.copy()
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_missing_required_arguments_exit_two(self):
        result = subprocess.run(
            ["python3", "-B", str(COMMAND)],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)

    def _commit_repository(self, message):
        self._commit_repository_at(self.repository, message)

    def _set_used_by_repositories(self, repositories):
        manifest = self.repository / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["used_by_repositories"] = repositories
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self._commit_repository("登记反查仓")

    @staticmethod
    def _make_git_repository(path):
        path.mkdir(parents=True)
        subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)

    @staticmethod
    def _add_alpha_child_feature(repository):
        manifest = repository / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        alpha = document["children"][0]
        alpha["children"] = [
            {
                "id": "alpha-child",
                "title": "Alpha 子功能",
                "what": "提供 Alpha 子功能。",
                "why": {
                    "text": "让 Alpha 子功能容易使用。",
                    "source": "原话",
                    "quote": "用户需要 Alpha 子功能。",
                    "from": "测试契约",
                },
                "directory": "alpha/child",
                "items": [
                    {
                        "id": "core",
                        "text": "用户可以使用 Alpha 子功能。",
                        "status": "确认",
                    }
                ],
                "children": [],
            }
        ]
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        child_file = repository / "alpha" / "child" / "main.py"
        child_file.parent.mkdir(parents=True)
        child_file.write_text("value = 3\n", encoding="utf-8")

    @staticmethod
    def _commit_repository_at(repository, message):
        status = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        if not status:
            return
        subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-m", message],
            check=True,
            capture_output=True,
            text=True,
        )

    def _record_and_commit(self, features, message):
        feature_ids = (
            ("root", "alpha", "beta")
            if features == "全部"
            else tuple(features.split())
            if isinstance(features, str)
            else tuple(features)
        )
        head = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        title = f"清扫决定：{self.repository.name} 计划提交 {head}"
        decisions = self.directory / "record-decisions.json"
        answers = self.directory / "record-answers.json"
        decision_items = [
            {
                "id": feature_id,
                "title": feature_id,
                "lines": ["认可。"],
                "options": ["照建议", "留"],
                "recommended": "照建议",
                "compare": None,
                "details": None,
            }
            for feature_id in feature_ids
        ]
        decision_document = {
            "title": title,
            "intro": ["逐项决定。"],
            "groups": [{"title": "功能", "notes": [], "items": decision_items}],
            "footnotes": [],
        }
        decisions.write_text(
            json.dumps(decision_document, ensure_ascii=False),
            encoding="utf-8",
        )
        answers.write_text(
            json.dumps(
                {
                    "title": title,
                    "answers": {
                        feature_id: {"option": "照建议", "note": ""}
                        for feature_id in feature_ids
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                "python3",
                "-B",
                str(XY_CLEANUP_DIRECTORY / "changed_features_only" / "approvals.py"),
                "record",
                "--repo",
                str(self.repository),
                "--decisions",
                str(decisions),
                "--answers",
                str(answers),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self._commit_repository(message)
        return subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
