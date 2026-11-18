import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "xy-repo-map" / "tests"))
sys.path.insert(0, str(ROOT / "xy-cleanup"))

from map_fixture import make_cleanup_repo  # noqa: E402
import decision_page  # noqa: E402


COMMAND = ROOT / "xy-cleanup" / "feature_cards.py"


def card(feature_id, **changes):
    value = {
        "feature": feature_id,
        "used": [f"实际使用中运行了 {feature_id}。"],
        "unused": [],
        "changed": [],
        "todo": [],
        "map_issues": [],
        "suggestion": [],
        "recommended": "留",
    }
    value.update(changes)
    return value


class FeatureCardsTest(unittest.TestCase):
    def setUp(self):
        self.repository = make_cleanup_repo(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
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
            "python3", "-B", str(COMMAND), "--repo", str(self.repository),
            "--scope", *scope, "--review", review,
        ]
        paths = self.write_cards(cards)
        return self.run_card_paths(paths, arguments, add_args)

    def run_card_paths(self, paths, arguments, add_args=()):
        arguments.extend(("--cards", *(str(path) for path in paths)))
        arguments.extend(("--output", str(self.output), *add_args))
        return subprocess.run(arguments, capture_output=True, text=True, check=False)

    def default_cards(self):
        return [card("root"), card("alpha"), card("beta")]

    def test_valid_cards_make_a_decision_page_accepted_by_decision_page(self):
        cards = self.default_cards()
        cards[1]["unused"] = [{
            "what": "可选的旧格式导出入口。",
            "evidence": "README 实际使用记录只选了新版入口。",
            "lines": 12,
            "risk": "旧调用方可能需要迁移。",
        }]
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
            items[2]["options"], ["照建议", "留", "按我写的改", "不要这个功能"],
        )
        self.assertEqual(items[1]["recommended"], "照建议")
        self.assertIn("可选的旧格式导出入口", "\n".join(items[1]["lines"]))
        self.assertIn("README 实际使用记录只选了新版入口", "\n".join(items[1]["lines"]))
        self.assertIn("前三项", "\n".join(decisions["intro"]))

    def test_page_lists_draft_items_and_reasons_and_shows_rewritten_item_text(self):
        self._commit_repository("初始提交")
        self._record_and_commit("alpha", "认可 alpha")
        manifest = self.repository / "repo-map" / "features.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        alpha = document["children"][0]
        alpha["why"] = {"text": "简化用户设置。", "source": "起草", "quote": "", "from": ""}
        alpha["items"][0]["text"] = "提供新版 alpha 功能。"
        alpha["items"][0]["status"] = "起草"
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "repo-map/features.json"], check=True)

        result = self.run_cards([card("alpha")], scope=("alpha",), review="变过的")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = json.loads(self.output.read_text(encoding="utf-8"))["groups"][0]["items"][0]
        rendered_lines = "\n".join(item["lines"])
        self.assertIn("提供新版 alpha 功能。", rendered_lines)
        self.assertIn("起草", rendered_lines)
        self.assertIn("简化用户设置。", rendered_lines)
        self.assertIn("原来：提供 alpha 功能。", rendered_lines)

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
            decisions["groups"][0]["items"][2]["recommended"], "不要这个功能",
        )

    def test_rejects_missing_extra_duplicate_and_unknown_cards(self):
        cases = (
            (self.default_cards()[:-1], ("全部",), "缺少卡片"),
            ([card("root"), card("alpha"), card("beta"), card("beta")],
             ("全部",), "重复卡片"),
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
            lambda value: value["unused"].append({
                "what": "不用的入口。", "evidence": "没有调用。", "lines": 1,
            }),
            lambda value: value["unused"].append({
                "what": "不用的入口。", "evidence": "没有调用。", "lines": 1,
                "risk": "无。", "extra": True,
            }),
            lambda value: value["unused"].append({
                "what": " ", "evidence": "没有调用。", "lines": 1, "risk": "无。",
            }),
            lambda value: value["unused"].append({
                "what": "不用的入口。", "evidence": "没有调用。", "lines": -1,
                "risk": "无。",
            }),
            lambda value: value["unused"].append({
                "what": "不用的入口。", "evidence": "没有调用。", "lines": True,
                "risk": "无。",
            }),
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
            "python3", "-B", str(COMMAND), "--repo", str(self.repository),
            "--scope", "全部", "--review", "全部",
        ]
        paths = self.write_cards(self.default_cards())
        paths[0].write_text('{"feature":"root","feature":"root"}', encoding="utf-8")

        result = self.run_card_paths(paths, arguments)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(self.output.exists())

        arguments = [
            "python3", "-B", str(COMMAND), "--repo", str(self.repository),
            "--scope", "全部", "--review", "全部",
        ]
        result = self.run_card_paths([self.directory / "absent.json"], arguments)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_missing_required_arguments_exit_two(self):
        result = subprocess.run(
            ["python3", "-B", str(COMMAND)], capture_output=True, text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)

    def _commit_repository(self, message):
        subprocess.run(["git", "-C", str(self.repository), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.repository), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.repository), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repository), "commit", "-m", message], check=True,
                       capture_output=True, text=True)

    def _record_and_commit(self, features, message):
        subprocess.run([
            "python3", "-B", str(ROOT / "xy-cleanup" / "approvals.py"), "record",
            "--repo", str(self.repository), "--features", features,
            "--kept-markers", "无",
        ], check=True, capture_output=True, text=True)
        self._commit_repository(message)
