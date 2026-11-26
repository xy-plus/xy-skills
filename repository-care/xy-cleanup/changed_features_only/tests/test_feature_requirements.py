import json
from pathlib import Path
import unittest


ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
FEATURE_MAP = ROOT / "repo-map" / "features.json"


def feature_items(document):
    result = {}

    def visit(feature):
        feature_id = feature["id"]
        result[feature_id] = {
            item["id"]: item for item in feature.get("items", [])
        }
        for child in feature.get("children", []):
            visit(child)

    visit(document)
    return result


class FeatureRequirementsTest(unittest.TestCase):
    def setUp(self):
        self.items = feature_items(
            json.loads(FEATURE_MAP.read_text(encoding="utf-8"))
        )

    def test_narrowed_requirements_keep_their_earlier_scope(self):
        original = {
            "cross-check-card-suggestions": "交卡前检查建议中的几项改动彼此不矛盾。",
            "unused-needs-positive-evidence": (
                "判没人用要两头查：从登记的使用来源往下追，也从待删内容往上反查引用它的地方；"
                "反查到登记外的使用者按在用写，本仓漏登的补登，别的仓漏登的报告出来由那个仓补登。"
            ),
            "retry-submit": "提交失败时保留当前填写并允许重试；提交成功后锁定答案并清除本页草稿。",
            "mount-and-clean-page": (
                "把决定页挂到指定平台并返回可打开链接，等待一份有效回答；"
                "提交、挂载失败或中断后清理本次服务和挂载。"
            ),
            "record-approved-state": (
                "审查完成后，根据决定清单和完整回答记录所答功能当时的提交，"
                "保留其他有效记录并删除已不存在的功能记录。"
            ),
            "three-things-up-front": (
                "每张卡片前面只有三行：图上的「要什么」、谁在用（含没人用的）、照建议会做；"
                "认可后改过的「要什么」附原来的写法。"
            ),
        }
        for item_id, old_text in original.items():
            with self.subTest(item=item_id):
                containing = [
                    item["text"]
                    for feature in self.items.values()
                    for current_id, item in feature.items()
                    if current_id == item_id
                ]
                self.assertEqual(len(containing), 1)
                self.assertIn(old_text, containing[0])

    def test_primary_checkout_requirements_are_drafted_in_both_features(self):
        cards = self.items["feature-cards"]
        approvals = self.items["changed-features-only"]
        expected = {
            "feature-cards": {
                "decision-title-primary-checkout-name": "决定页标题使用主检出仓名。"
            },
            "changed-features-only": {
                "record-prefix-primary-checkout-name": "record 的决定标题前缀使用主检出仓名。",
                "merge-mainline-without-rebase": "出页后只把主线合并进清扫分支，不变基。",
            },
        }
        actual = {"feature-cards": cards, "changed-features-only": approvals}
        for feature_id, requirements in expected.items():
            for item_id, text in requirements.items():
                with self.subTest(item=item_id):
                    item = actual[feature_id].get(item_id)
                    self.assertIsNotNone(item)
                    self.assertEqual(item["text"], text)
                    self.assertEqual(item["status"], "起草")


if __name__ == "__main__":
    unittest.main()
