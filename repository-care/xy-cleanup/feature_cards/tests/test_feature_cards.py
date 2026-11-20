import json
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
        "used": [f"实际使用中运行了 {feature_id}。"],
        "unused": [],
        "changed": [],
        "todo": [],
        "map_issues": {
            "why": "成立：说明本功能如何回答用户为什么需要它。",
            "what": "一致：what 与 why 和条目讲的是同一件事。",
            "group": "合适：列出组内功能后确认没有漏项。",
            "items": [],
        },
        "suggestion": [],
        "recommended": "留",
    }
    value.update(changes)
    return value


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

    def run_cards(self, cards, scope=("全部",), review="全部", add_args=()):
        arguments = [
            "python3",
            "-B",
            str(COMMAND),
            "--repo",
            str(self.repository),
            "--scope",
            *scope,
            "--review",
            review,
        ]
        paths = self.write_cards(cards)
        return self.run_card_paths(paths, arguments, add_args)

    def run_card_paths(self, paths, arguments, add_args=()):
        arguments.extend(("--cards", *(str(path) for path in paths)))
        arguments.extend(("--output", str(self.output), *add_args))
        return subprocess.run(arguments, capture_output=True, text=True, check=False)

    def default_cards(self):
        return [card("root"), card("alpha"), card("beta")]

    def test_cards_use_the_shared_json_shape_checker(self):
        with patch.object(
            feature_cards,
            "check_value",
            wraps=feature_cards.check_value,
        ) as shared_check:
            problems = feature_cards._card_errors(card("alpha"), "卡片")

        self.assertEqual(problems, [])
        self.assertTrue(shared_check.called)

    def test_map_issues_require_three_judgments_and_string_items(self):
        mutations = (
            lambda value: value.update(map_issues=[]),
            lambda value: value["map_issues"].pop("why"),
            lambda value: value["map_issues"].update(why="  "),
            lambda value: value["map_issues"].update(why="成立"),
            lambda value: value["map_issues"].update(what="一致"),
            lambda value: value["map_issues"].update(group="合适"),
            lambda value: value["map_issues"].update(why="成立：  "),
            lambda value: value["map_issues"].update(what=None),
            lambda value: value["map_issues"].update(group=1),
            lambda value: value["map_issues"].update(items="条目"),
            lambda value: value["map_issues"].update(items=["有效条目", 3]),
            lambda value: value["map_issues"].update(extra="多余字段"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = card("alpha")
                mutate(value)
                self.assertTrue(feature_cards._card_errors(value, "卡片"))

    def test_page_shows_only_failed_map_judgments_and_each_item_issue(self):
        cards = self.default_cards()
        cards[0]["map_issues"]["why"] = "理由只抱怨旧版效果，没有说明功能为何必要。"
        cards[1]["map_issues"] = {
            "why": "成立：理由具体回答了为什么需要这个功能。",
            "what": "what 与理由矛盾：理由说免点，what 却只写看依赖。",
            "group": "合适：列出组内功能并确认没有漏项。",
            "items": ["漏了运行目录管理。", "写了但未实现的结果。"],
        }
        cards[2]["map_issues"]["group"] = "运行目录管理应该归入本组，但图中遗漏。"

        result = self.run_cards(cards)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        items = json.loads(self.output.read_text(encoding="utf-8"))["groups"][0][
            "items"
        ]
        root_lines = "\n".join(items[0]["lines"])
        self.assertIn(
            "图上理由不成立：理由只抱怨旧版效果，没有说明功能为何必要。",
            root_lines,
        )
        self.assertNotIn("图上 what 不一致", root_lines)
        self.assertNotIn("图上分组不合适", root_lines)

        alpha_lines = "\n".join(items[1]["lines"])
        self.assertIn(
            "图上 what 不一致：what 与理由矛盾：理由说免点，what 却只写看依赖。",
            alpha_lines,
        )
        self.assertIn("图上条目问题：漏了运行目录管理。", alpha_lines)
        self.assertIn("图上条目问题：写了但未实现的结果。", alpha_lines)
        self.assertNotIn("图上理由不成立", alpha_lines)
        self.assertNotIn("图上分组不合适", alpha_lines)

        beta_lines = "\n".join(items[2]["lines"])
        self.assertIn(
            "图上分组不合适：运行目录管理应该归入本组，但图中遗漏。",
            beta_lines,
        )
        self.assertNotIn("图上理由不成立", beta_lines)
        self.assertNotIn("图上 what 不一致", beta_lines)

    def test_valid_cards_make_a_decision_page_accepted_by_decision_page(self):
        cards = self.default_cards()
        cards[1]["unused"] = [
            {
                "what": "可选的旧格式导出入口。",
                "evidence": "README 实际使用记录只选了新版入口。",
                "lines": 12,
                "risk": "旧调用方可能需要迁移。",
            }
        ]
        cards[1]["suggestion"] = ["删除旧格式导出入口。"]
        cards[1]["recommended"] = "照建议"

        result = self.run_cards(cards)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        decisions = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertIs(decision_page.validate_decisions(decisions), decisions)
        items = decisions["groups"][0]["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual([item["id"] for item in items], ["root", "alpha", "beta"])
        self.assertEqual(items[0]["options"], ["照建议", "留", "按我写的改"])
        self.assertEqual(
            items[2]["options"],
            ["照建议", "留", "按我写的改", "不要这个功能"],
        )
        self.assertEqual(items[1]["recommended"], "照建议")
        intro = "\n".join(decisions["intro"])
        for choice in ("照建议：", "留：", "按我写的改：", "不要这个功能：", "前三项"):
            self.assertIn(choice, intro)
        self.assertIn("为什么：让功能容易使用。", "\n".join(items[0]["lines"]))
        self.assertIn("可选的旧格式导出入口", "\n".join(items[1]["lines"]))
        self.assertIn("README 实际使用记录只选了新版入口", "\n".join(items[1]["lines"]))
        self.assertIn("前三项", "\n".join(decisions["intro"]))

    def test_page_lists_draft_items_and_reasons_and_shows_rewritten_item_text(self):
        self._commit_repository("初始提交")
        approval_commit = self._record_and_commit("全部", "认可全图")
        manifest = self.repository / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        alpha = document["children"][0]
        alpha["why"] = {
            "text": "简化用户设置。",
            "source": "起草",
            "quote": "",
            "from": "",
        }
        alpha["items"][0]["text"] = "提供新版 alpha 功能。"
        alpha["items"][0]["status"] = "起草"
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self._commit_repository("更新 alpha 草稿")

        result = self.run_cards([card("alpha")], scope=("alpha",), review="变过的")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = json.loads(self.output.read_text(encoding="utf-8"))["groups"][0][
            "items"
        ][0]
        rendered_lines = "\n".join(item["lines"])
        self.assertIn("提供新版 alpha 功能。", rendered_lines)
        self.assertIn("起草", rendered_lines)
        self.assertIn("简化用户设置。", rendered_lines)
        self.assertIn("原来：提供 alpha 功能。", rendered_lines)
        self.assertNotIn(approval_commit, rendered_lines)

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
            decisions["groups"][0]["items"][2]["recommended"],
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

    def test_rejects_malformed_card_fields(self):
        mutations = (
            lambda value: value.pop("todo"),
            lambda value: value.update(extra="不允许"),
            lambda value: value["used"].__setitem__(0, "  "),
            lambda value: value["unused"].append(
                {
                    "what": "不用的入口。",
                    "evidence": "没有调用。",
                    "lines": 1,
                }
            ),
            lambda value: value["unused"].append(
                {
                    "what": "不用的入口。",
                    "evidence": "没有调用。",
                    "lines": 1,
                    "risk": "无。",
                    "extra": True,
                }
            ),
            lambda value: value["unused"].append(
                {
                    "what": " ",
                    "evidence": "没有调用。",
                    "lines": 1,
                    "risk": "无。",
                }
            ),
            lambda value: value["unused"].append(
                {
                    "what": "不用的入口。",
                    "evidence": "没有调用。",
                    "lines": -1,
                    "risk": "无。",
                }
            ),
            lambda value: value["unused"].append(
                {
                    "what": "不用的入口。",
                    "evidence": "没有调用。",
                    "lines": True,
                    "risk": "无。",
                }
            ),
            lambda value: value.update(recommended="照建议"),
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
        arguments = [
            "python3",
            "-B",
            str(COMMAND),
            "--repo",
            str(self.repository),
            "--scope",
            "全部",
            "--review",
            "全部",
        ]
        paths = self.write_cards(self.default_cards())
        paths[0].write_text('{"feature":"root","feature":"root"}', encoding="utf-8")

        result = self.run_card_paths(paths, arguments)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(self.output.exists())

        arguments = [
            "python3",
            "-B",
            str(COMMAND),
            "--repo",
            str(self.repository),
            "--scope",
            "全部",
            "--review",
            "全部",
        ]
        result = self.run_card_paths([self.directory / "absent.json"], arguments)
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
        decisions.write_text(
            json.dumps(
                {
                    "title": title,
                    "intro": ["逐项决定。"],
                    "groups": [
                        {
                            "title": "功能",
                            "notes": [],
                            "items": [
                                {
                                    "id": feature_id,
                                    "title": feature_id,
                                    "lines": ["认可。"],
                                    "options": ["照建议", "留"],
                                    "recommended": "照建议",
                                    "compare": None,
                                }
                                for feature_id in feature_ids
                            ],
                        }
                    ],
                    "footnotes": [],
                },
                ensure_ascii=False,
            ),
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
