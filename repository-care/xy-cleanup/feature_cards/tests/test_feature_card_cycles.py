from pathlib import Path
import sys
import unittest

ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()
)
sys.path.insert(0, str(ROOT / "repository-care" / "xy-cleanup" / "feature_cards"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_cards  # noqa: E402
import test_feature_cards as card_fixtures  # noqa: E402


class FeatureCardCycleTest(unittest.TestCase):
    def test_decision_front_names_the_other_features_in_a_product_cycle(self):
        alpha = {
            "title": "Alpha",
            "parent": "root",
            "what": "Alpha behavior",
            "why": {"text": "Reason", "source": "原话"},
            "items": [],
            "children": [],
            "cycle_with": ["beta"],
        }
        features = {
            "root": {"title": "Root", "parent": None},
            "alpha": alpha,
            "beta": {"title": "Beta", "parent": "root"},
        }
        context = feature_cards.DecisionContext(
            values={"alpha": alpha},
            data={"features": features},
            previous_features={},
            commits={"alpha": "无"},
        )
        card = card_fixtures.card("alpha", used=[])

        decision = feature_cards._decision_item("alpha", card, context)

        self.assertEqual(
            decision["lines"][1],
            "互相引用\t和 Beta 成环（产品代码 import）",
        )
