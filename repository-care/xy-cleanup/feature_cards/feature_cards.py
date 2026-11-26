import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import tempfile


XY_CLEANUP_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(XY_CLEANUP_DIRECTORY))
from cleanup_imports import setup_cleanup_imports  # noqa: E402

setup_cleanup_imports(__file__, ("decision_page",))
from approvals import plan_data  # noqa: E402
from cli_errors import report_cli_argument_error  # noqa: E402
from decision_page import validate_decisions  # noqa: E402
from json_validation import check_value, object_without_duplicate_keys  # noqa: E402
from repo_map_lib.git_repository import (  # noqa: E402
    ArgumentError,
    primary_checkout_name,
    primary_checkout_path,
    require_repository_root,
)
from repo_map_lib.validate_feature_list.features import (  # noqa: E402
    FEATURE_ID,
    feature_chain,
    features_by_id,
)


CARD_SHAPE = {
    "feature": "",
    "used": [{"who": "", "evidence": ""}],
    "unused": [
        {
            "what": "",
            "evidence": "",
            "reverse_search": "",
            "lines": "",
            "risk": "",
        }
    ],
    "changed": [""],
    "todo": [""],
    "map_issues": [""],
    "suggestion": [""],
    "cross_feature_changes": [{"feature": "", "change": ""}],
    "item_changes": [{"item": "", "text": ""}],
    "recommended": "",
}
RECOMMENDATIONS = {"照建议", "留", "不要这个功能"}
COMMON_OPTIONS = ["留", "留，下次还问我", "按我写的改"]
REMOVE_FEATURE = "不要这个功能"


class InvalidCardJson(ValueError):
    pass


def parser():
    result = argparse.ArgumentParser(description="校验功能卡片并拼成用户决定清单")
    result.add_argument("--repo", required=True, help="仓根的绝对路径")
    result.add_argument(
        "--scope", required=True, nargs="+", help="功能 id 列表，或单独写 全部"
    )
    result.add_argument("--review", required=True, choices=("变过的", "全部"))
    result.add_argument(
        "--cards", required=True, nargs="+", help="每个功能卡片 JSON 路径"
    )
    result.add_argument("--output", required=True, help="决定清单 JSON 路径")
    return result


def _card_errors(card, label):
    shape_view = _card_shape_view(card)
    problems = check_value(label, shape_view, CARD_SHAPE)
    if not isinstance(card, dict):
        return problems
    problems.extend(_card_usage_errors(card, label))
    problems.extend(_card_recommendation_error(card, label))
    problems.extend(_item_change_errors(card, label))
    return problems


def _card_usage_errors(card, label):
    problems = []
    used = card.get("used")
    if isinstance(used, list) and len(used) > 3:
        problems.append(f"{label}.used 最多 3 条")
    unused = card.get("unused")
    if isinstance(unused, list):
        for index, value in enumerate(unused):
            if not isinstance(value, dict):
                continue
            lines = value.get("lines")
            if type(lines) is not int or lines < 0:
                problems.append(f"{label}.unused[{index}].lines 必须是非负整数")
    return problems


def _card_recommendation_error(card, label):
    problems = []
    recommendation = card.get("recommended")
    if not isinstance(recommendation, str) or recommendation not in RECOMMENDATIONS:
        problems.append(f"{label}.recommended 必须是 照建议、留、不要这个功能 之一")
    elif recommendation == "照建议" and not (
        card.get("suggestion")
        or card.get("map_issues")
        or card.get("item_changes")
    ):
        problems.append(
            f"{label}.recommended 为 照建议 时 suggestion、map_issues 和 item_changes 不能都空"
        )
    return problems


def _item_change_errors(card, label):
    problems = []
    item_changes = card.get("item_changes")
    if isinstance(item_changes, list):
        seen_items = set()
        for index, value in enumerate(item_changes):
            if not isinstance(value, dict):
                continue
            item_id = value.get("item")
            if not isinstance(item_id, str) or FEATURE_ID.fullmatch(item_id) is None:
                problems.append(
                    f"{label}.item_changes[{index}].item id 必须匹配 "
                    "^[a-z][a-z0-9-]*$"
                )
            elif item_id in seen_items:
                problems.append(f"{label}.item_changes 的 item id 重复：{item_id}")
            else:
                seen_items.add(item_id)
            text = value.get("text")
            if isinstance(text, str) and ("\n" in text or "\r" in text):
                problems.append(
                    f"{label}.item_changes[{index}].text 必须是一行文字"
                )
    return problems


def _card_shape_view(card):
    if not isinstance(card, dict):
        return card
    result = dict(card)
    unused = result.get("unused")
    if isinstance(unused, list):
        result["unused"] = [
            dict(item) if isinstance(item, dict) else item for item in unused
        ]
        for item in result["unused"]:
            if isinstance(item, dict) and type(item.get("lines")) is int:
                item["lines"] = str(item["lines"])
    return result


def _read_cards(paths):
    cards = []
    for path in paths:
        card_path = Path(path)
        if not card_path.is_file():
            raise FileNotFoundError(f"卡片文件不存在：{card_path}")
        try:
            cards.append(
                json.loads(
                    card_path.read_text(encoding="utf-8"),
                    object_pairs_hook=object_without_duplicate_keys,
                )
            )
        except (OSError, UnicodeError, ValueError) as error:
            raise InvalidCardJson(f"卡片不是合法 JSON：{card_path}：{error}") from error
    return cards


def _load_previous_features(repository, commit):
    if commit == "无":
        return {}
    result = subprocess.run(
        ["git", "-C", str(repository), "show", f"{commit}:repo-map/features.json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(
            f"无法读取上次认可提交 {commit} 的功能清单：{result.stderr.strip()}"
        )
    try:
        return features_by_id(
            json.loads(
                result.stdout,
                object_pairs_hook=object_without_duplicate_keys,
            )
        )
    except ValueError as error:
        raise ValueError(
            f"上次认可提交 {commit} 的功能清单不是合法 JSON：{error}"
        ) from error


def _reason_detail_lines(feature, previous_feature, *, unapproved):
    lines = []
    why = feature["why"]
    why_text = why["text"]
    if why["source"] == "起草":
        why_text += "（起草，等你拍板）"
    lines.append(f"为什么\t{why_text}")
    if not unapproved and why["source"] == "起草":
        previous_why = previous_feature.get("why") if previous_feature else None
        if isinstance(previous_why, dict) and previous_why.get("text") != why["text"]:
            lines.append(f"原来\t{previous_why['text']}")
    return lines


def _draft_item_lines(feature, previous_feature):
    lines = []
    previous_items = (
        {
            item["id"]: item
            for item in previous_feature.get("items", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if previous_feature
        else {}
    )
    for item in feature["items"]:
        if item["status"] != "起草":
            continue
        text = f"起草条目 {item['id']}\t{item['text']}"
        previous = previous_items.get(item["id"])
        if previous is not None and previous.get("text") != item["text"]:
            text += f"（原来：{previous['text']}）"
        lines.append(text)
    return lines


def _join_front_sentences(values):
    return "；".join(value.removesuffix("。") for value in values) + "。"


def _feature_title(feature_id, features):
    return " › ".join(
        features[ancestor_id]["title"]
        for ancestor_id in reversed(list(feature_chain(feature_id, features)))
    )


def _front_card_lines(card, features):
    lines = []
    if card["used"] or card["unused"]:
        who = (
            _join_front_sentences([item["who"] for item in card["used"]])
            if card["used"]
            else ""
        )
        if card["unused"]:
            unused = _join_front_sentences(
                [item["what"] for item in card["unused"]]
            )
            who += f"没人用：{unused}"
        lines.append(f"谁在用\t{who}")
    if card["suggestion"] or card["map_issues"]:
        actions = list(card["suggestion"])
        if card["map_issues"]:
            actions.append(f"改图上文字 {len(card['map_issues'])} 处，见细节")
        lines.append(f"照建议会做\t{_join_front_sentences(actions)}")
    if card["cross_feature_changes"]:
        changes = [
            f"{_feature_title(item['feature'], features)}：{item['change']}"
            for item in card["cross_feature_changes"]
        ]
        lines.append(f"连带改动\t{_join_front_sentences(changes)}")
    return lines


def _item_change_lines(feature, previous_feature, card):
    current_items = {
        item["id"]: item
        for item in feature.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    previous_items = {
        item["id"]: item
        for item in (previous_feature or {}).get("items", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    lines = []
    for change in card["item_changes"]:
        line = f"要改的条目 {change['item']}\t{change['text']}"
        old_item = current_items.get(change["item"])
        if old_item is None or old_item.get("text") == change["text"]:
            old_item = previous_items.get(change["item"])
        if old_item is not None and old_item.get("text") != change["text"]:
            line += f"（原来：{old_item['text']}）"
        lines.append(line)
    return lines


def _detail_card_lines(card):
    lines = [
        f"在用的依据\t{item['evidence']}"
        for item in card["used"]
    ]
    for item in card["unused"]:
        lines.extend(
            [
                f"没人用的依据\t{item['evidence']}",
                f"反查的依据\t{item['reverse_search']}",
                f"可减\t{item['lines']} 行",
                f"风险\t{item['risk']}",
            ]
        )
    lines.extend(f"图上要改\t{value}" for value in card["map_issues"])
    lines.extend(f"TODO 大问题\t{value}" for value in card["todo"])
    return lines


def _details_title(feature, card, *, unapproved, draft_items):
    title_parts = [
        "理由（等你拍板）" if feature["why"]["source"] == "起草" else "理由"
    ]
    if not unapproved and card["changed"]:
        title_parts.append(f"变了什么 {len(card['changed'])} 条")
    evidence_count = len(card["used"]) + 2 * len(card["unused"])
    if evidence_count:
        title_parts.append(f"依据 {evidence_count} 条")
    removable_lines = sum(item["lines"] for item in card["unused"])
    if removable_lines > 0:
        title_parts.append(f"可减 {removable_lines} 行")
    if card["unused"]:
        title_parts.append(f"风险 {len(card['unused'])} 条")
    if card["map_issues"]:
        title_parts.append(f"图上要改 {len(card['map_issues'])} 处")
    if card["todo"]:
        title_parts.append(f"TODO 大问题 {len(card['todo'])} 条")
    if unapproved and draft_items:
        title_parts.append(f"起草条目 {len(draft_items)} 条")
    return "细节：" + "、".join(title_parts)


def _decision_item(feature_id, card, context):
    feature = context.values[feature_id]
    features = context.data["features"]
    title = _feature_title(feature_id, features)
    previous_feature = context.previous_features.get(feature_id)
    unapproved = context.commits[feature_id] == "无"
    draft_items = _draft_item_lines(feature, previous_feature)
    lines = [f"要什么\t{feature['what']}"]
    if not unapproved and previous_feature is not None:
        previous_what = previous_feature.get("what")
        if previous_what != feature["what"]:
            lines.append(f"原来\t{previous_what}")
    if features[feature_id]["cycle_with"]:
        other_titles = "、".join(
            features[other_id]["title"]
            for other_id in features[feature_id]["cycle_with"]
        )
        lines.append(
            f"互相引用\t和 {other_titles} 成环（产品代码 import）"
        )
    for source_id in context.planned_ids:
        source_card = context.cards[source_id]
        source_title = _feature_title(source_id, features)
        lines.extend(
            f"会被连带改\t{source_title} 的建议要改：{change['change']}"
            for change in source_card["cross_feature_changes"]
            if change["feature"] == feature_id
        )
    lines.extend(_front_card_lines(card, features))
    lines.extend(_item_change_lines(feature, previous_feature, card))
    if not unapproved:
        lines.extend(draft_items)
    detail_lines = _reason_detail_lines(
        feature, previous_feature, unapproved=unapproved
    )
    if not unapproved:
        detail_lines.extend(f"变了什么\t{value}" for value in card["changed"])
    detail_lines.extend(_detail_card_lines(card))
    if unapproved:
        detail_lines.extend(draft_items)
    details = {
        "title": _details_title(
            feature,
            card,
            unapproved=unapproved,
            draft_items=draft_items,
        ),
        "lines": detail_lines,
    }
    options = list(COMMON_OPTIONS)
    if card["suggestion"] or card["map_issues"] or card["item_changes"]:
        options.insert(0, "照建议")
    if not context.data["features"][feature_id]["children"]:
        options.append(REMOVE_FEATURE)
    return {
        "id": feature_id,
        "title": title,
        "lines": lines,
        "options": options,
        "recommended": card["recommended"],
        "details": details,
        "compare": None,
    }


@dataclass
class DecisionContext:
    values: dict
    data: dict
    previous_features: dict
    commits: dict
    cards: dict
    planned_ids: list


@dataclass
class DecisionPlan:
    repository: str
    data: dict
    values: dict
    planned: list
    commits: dict
    plan_commit: str


def _validate_card_set(cards, planned_ids, data, commits):
    by_id = {}
    problems = []
    for index, card in enumerate(cards):
        label = f"卡片[{index}]"
        problems.extend(_card_errors(card, label))
        if not isinstance(card, dict):
            continue
        feature_id = card.get("feature")
        if not isinstance(feature_id, str):
            continue
        if feature_id in by_id:
            problems.append(f"重复卡片：{feature_id}")
        by_id[feature_id] = card

    problems.extend(_card_membership_errors(by_id, planned_ids, data))
    problems.extend(_card_cross_feature_errors(by_id, planned_ids, data))
    for feature_id in planned_ids:
        card = by_id.get(feature_id)
        if commits[feature_id] == "无" and isinstance(card, dict) and card.get("changed"):
            problems.append(f"{feature_id} 没有认可记录，changed 必须为 []")
    problems.extend(_card_recommendation_errors(by_id, planned_ids, data))
    return by_id, problems


def _card_membership_errors(by_id, planned_ids, data):
    problems = []
    planned_set = set(planned_ids)
    for feature_id in sorted(set(by_id) - set(data["features"])):
        problems.append(f"未知功能卡片：{feature_id}")
    for feature_id in sorted(set(by_id) - planned_set):
        if feature_id in data["features"]:
            problems.append(f"卡片不在本次计划中：{feature_id}")
    for feature_id in planned_ids:
        if feature_id not in by_id:
            problems.append(f"计划功能缺少卡片：{feature_id}")
    return problems


def _card_cross_feature_errors(by_id, planned_ids, data):
    problems = []
    for feature_id in planned_ids:
        card = by_id.get(feature_id)
        if not isinstance(card, dict):
            continue
        changes = card.get("cross_feature_changes")
        if not isinstance(changes, list):
            continue
        for index, change in enumerate(changes):
            if not isinstance(change, dict):
                continue
            target = change.get("feature")
            if not isinstance(target, str):
                continue
            if target not in data["features"]:
                problems.append(
                    f"{feature_id}.cross_feature_changes[{index}].feature "
                    f"不是图上的功能：{target}"
                )
            elif target == feature_id:
                problems.append(
                    f"{feature_id}.cross_feature_changes[{index}].feature "
                    "不能是本卡功能"
                )
    return problems


def _card_recommendation_errors(by_id, planned_ids, data):
    problems = []
    for feature_id in planned_ids:
        card = by_id.get(feature_id)
        if not isinstance(card, dict):
            continue
        if (
            card.get("recommended") == REMOVE_FEATURE
            and data["features"][feature_id]["children"]
        ):
            problems.append(f"有子功能的上级不能推荐「不要这个功能」：{feature_id}")
    return problems


def _load_feature_history(repository, planned_ids, commits):
    previous_features = {}
    previous_by_commit = {}
    for feature_id in planned_ids:
        commit = commits[feature_id]
        if commit != "无":
            if commit not in previous_by_commit:
                previous_by_commit[commit] = _load_previous_features(repository, commit)
            previous = previous_by_commit[commit]
            previous_features[feature_id] = previous.get(feature_id)

    return previous_features


def _build_decision_items(planned, by_id, context):
    items = [
        _decision_item(
            feature_id,
            by_id[feature_id],
            context,
        )
        for feature_id, _reason in planned
    ]
    return items


def build_decisions(plan, cards):
    planned_ids = [feature_id for feature_id, _reason in plan.planned]
    by_id, problems = _validate_card_set(cards, planned_ids, plan.data, plan.commits)
    if problems:
        return None, problems

    previous_features = _load_feature_history(
        plan.repository, planned_ids, plan.commits
    )
    context = DecisionContext(
        plan.values,
        plan.data,
        previous_features,
        plan.commits,
        by_id,
        planned_ids,
    )
    items = _build_decision_items(plan.planned, by_id, context)
    change_items = [item for item in items if item["recommended"] != "留"]
    keep_items = [item for item in items if item["recommended"] == "留"]
    groups = []
    if change_items:
        groups.append(
            {
                "title": f"建议改或删（{len(change_items)} 个）",
                "notes": [],
                "items": change_items,
            }
        )
    if keep_items:
        groups.append(
            {
                "title": f"建议留着不动（{len(keep_items)} 个）",
                "notes": [],
                "items": keep_items,
            }
        )
    reason_counts = {}
    for _feature_id, reason in plan.planned:
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    reason_summary = "、".join(
        f"{reason} {count} 个" for reason, count in reason_counts.items()
    )
    first_intro = f"本页 {len(items)} 个功能：{reason_summary}。"
    if change_items and keep_items:
        first_intro += "建议改或删的排在前面。"
    decisions = {
        "title": (
            f"清扫决定：{primary_checkout_name(plan.repository)} "
            f"计划提交 {plan.plan_commit}"
        ),
        "intro": [
            first_intro,
            "每个功能选一项；写了备注就以备注为准。",
            "选「照建议」「留」「按我写的改」都表示认可这张卡片上的「要什么」、理由和起草条目，收在细节里的也算；不对的地方写进备注。",
            "「留，下次还问我」不算认可，下次清扫会再出卡片。",
            "「留」不动代码、测试、文档和 TODO；「不要这个功能」连图上的登记一起删，只有没有子功能的功能能选。",
        ],
        "groups": groups,
        "footnotes": [],
    }
    return validate_decisions(decisions), []


def _unavailable_reverse_repositories(repository, relative_paths):
    primary_checkout = primary_checkout_path(repository)
    unavailable = []
    for relative_path in relative_paths:
        candidate = (primary_checkout / relative_path).resolve()
        try:
            require_repository_root(candidate)
        except (ArgumentError, OSError):
            unavailable.append(str(candidate))
    return unavailable


def _write_output(path, decisions):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(decisions, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        temporary_file.write(content)
    temporary_path.replace(output_path)


def main(argv):
    options = parser().parse_args(argv)
    try:
        data, values, _records, planned, commits, plan_commit = plan_data(
            options.repo,
            options.scope,
            options.review,
        )
        if not planned:
            raise ValueError("范围内没有变过的功能，不用出页")
        cards = _read_cards(options.cards)
        has_unused = any(
            isinstance(card, dict)
            and isinstance(card.get("unused"), list)
            and card["unused"]
            for card in cards
        )
        root_values = values[data["root"]]
        if has_unused and "used_by_repositories" not in root_values:
            raise ValueError(
                "根功能还没登记 used_by_repositories，反查范围没定，不能判没人用；"
                "先按清扫第 2 步问用户，按回答写进去（不查别的仓写 []）。"
            )
        used_by_repositories = root_values.get("used_by_repositories", [])
        if has_unused and used_by_repositories:
            unavailable = _unavailable_reverse_repositories(
                options.repo,
                used_by_repositories,
            )
            if unavailable:
                raise ValueError(
                    "反查不完整，不能判没人用；登记的仓找不到或不是 git 仓："
                    + ", ".join(unavailable)
                )
        decisions, problems = build_decisions(
            DecisionPlan(
                options.repo,
                data,
                values,
                planned,
                commits,
                plan_commit,
            ),
            cards,
        )
        if problems:
            print("\n".join(problems))
            return 1
        _write_output(options.output, decisions)
    except ArgumentError as error:
        return report_cli_argument_error(error)
    except FileNotFoundError as error:
        print(str(error), file=sys.stderr)
        return 2
    except InvalidCardJson as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"已写决定清单：{options.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
