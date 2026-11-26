"""Test revised card output and validation."""

import json
import unittest

import test_feature_cards as support


card = support.card


class FeatureCardChangesTest(unittest.TestCase):
    def setUp(self):
        self.fixture = support.FeatureCardsTest("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def __getattr__(self, name):
        fixture = self.__dict__.get("fixture")
        if fixture is not None and hasattr(fixture, name):
            return getattr(fixture, name)
        raise AttributeError(name)


    def test_decision_title_uses_primary_checkout_name_from_a_linked_worktree(self):
        cards = self.default_cards()

        result = self.run_cards(cards)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        title = json.loads(self.output.read_text(encoding="utf-8"))["title"]
        self.assertTrue(title.startswith(f"清扫决定：{self.primary.name} 计划提交 "))

    def test_item_without_advice_has_no_follow_recommendation_option(self):
        result = self.run_cards(self.default_cards())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        items = json.loads(self.output.read_text(encoding="utf-8"))["groups"][0][
            "items"
        ]
        self.assertEqual(items[0]["options"], ["留", "留，下次还问我", "按我写的改"])
        self.assertEqual(
            items[1]["options"],
            ["留", "留，下次还问我", "按我写的改", "不要这个功能"],
        )

    def test_page_top_explains_that_reasking_does_not_record_approval(self):
        result = self.run_cards(self.default_cards())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        intro = json.loads(self.output.read_text(encoding="utf-8"))["intro"]
        self.assertEqual(
            intro,
            [
                "本页 3 个功能：全部重审 3 个。",
                "每个功能选一项；写了备注就以备注为准。",
                "选「照建议」「留」「按我写的改」都表示认可这张卡片上的「要什么」、理由和起草条目，收在细节里的也算；不对的地方写进备注。",
                "「留，下次还问我」不算认可，下次清扫会再出卡片。",
                "「留」不动代码、测试、文档和 TODO；「不要这个功能」连图上的登记一起删，只有没有子功能的功能能选。",
            ],
        )

    def test_item_changes_are_shown_and_make_follow_option_available(self):
        result = self.run_cards(
            [
                card(
                    "alpha",
                    item_changes=[
                        {"item": "core", "text": "提供新的 alpha 功能。"}
                    ],
                    recommended="照建议",
                )
            ],
            scope=("alpha",),
            review="全部",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = self.first_decision_item()
        self.assertIn("照建议", item["options"])
        self.assertIn(
            "要改的条目 core\t提供新的 alpha 功能。（原来：提供 alpha 功能。）",
            item["lines"],
        )

    def test_cross_feature_changes_are_shown_on_both_cards(self):
        result = self.run_cards(
            [
                card(
                    "alpha",
                    suggestion=["调整 alpha 的代码。"],
                    cross_feature_changes=[
                        {"feature": "beta", "change": "更新 Beta 的调用方式。"}
                    ],
                    recommended="照建议",
                ),
                card("beta"),
            ],
            scope=("alpha", "beta"),
            review="全部",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        items = {
            item["id"]: item
            for group in json.loads(self.output.read_text(encoding="utf-8"))["groups"]
            for item in group["items"]
        }
        self.assertIn(
            "连带改动\tRoot › Beta：更新 Beta 的调用方式。", items["alpha"]["lines"]
        )
        self.assertIn(
            "会被连带改\tRoot › Alpha 的建议要改：更新 Beta 的调用方式。",
            items["beta"]["lines"],
        )

    def test_rejects_cross_feature_changes_outside_the_map_or_to_self(self):
        cases = (
            (
                [{"feature": "missing", "change": "改另一项。"}],
                "不是图上的功能",
            ),
            (
                [{"feature": "alpha", "change": "改本项。"}],
                "不能是本卡功能",
            ),
            ([{"feature": "beta", "change": "  "}], "不能为空"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                result = self.run_cards(
                    [card("alpha", cross_feature_changes=changes)],
                    scope=("alpha",),
                    review="全部",
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stdout + result.stderr)

    def test_rejects_invalid_item_change_ids_text_and_duplicates(self):
        cases = (
            ([{"item": "Bad_ID", "text": "一行。"}], "id"),
            ([{"item": "new-item", "text": "第一行。\n第二行。"}], "一行"),
            (
                [
                    {"item": "same", "text": "第一版。"},
                    {"item": "same", "text": "第二版。"},
                ],
                "重复",
            ),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                result = self.run_cards(
                    [card("alpha", item_changes=changes)],
                    scope=("alpha",),
                    review="全部",
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stdout + result.stderr)

    def test_follow_recommendation_requires_some_advice(self):
        cards = [card("alpha", recommended="照建议")]

        result = self.run_cards(cards, scope=("alpha",), review="全部")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "卡片[0].recommended 为 照建议 时 suggestion、map_issues 和 item_changes 不能都空",
            result.stdout + result.stderr,
        )

    def test_rejects_malformed_card_fields(self):
        mutations = (
            lambda value: value.pop("todo"),
            lambda value: value.pop("cross_feature_changes"),
            lambda value: value.pop("item_changes"),
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
