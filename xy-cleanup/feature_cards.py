import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "xy-repo-map"))
sys.path.insert(0, str(REPOSITORY / "xy-handoff"))
from approvals import plan_data  # noqa: E402
from check_handoff import object_without_duplicate_keys  # noqa: E402
from decision_page import validate_decisions  # noqa: E402
from repo_map_lib.git_repository import ArgumentError  # noqa: E402


CARD_KEYS = {
    "feature", "used", "unused", "changed", "todo", "map_issues",
    "suggestion", "recommended",
}
UNUSED_KEYS = {"what", "evidence", "lines", "risk"}
RECOMMENDATIONS = {"照建议", "留", "不要这个功能"}
COMMON_OPTIONS = ["照建议", "留", "按我写的改"]
REMOVE_FEATURE = "不要这个功能"


class InvalidCardJson(ValueError):
    pass


def parser():
    result = argparse.ArgumentParser(description="校验功能卡片并拼成用户决定清单")
    result.add_argument("--repo", required=True, help="仓根的绝对路径")
    result.add_argument("--scope", required=True, nargs="+", help="功能 id 列表，或单独写 全部")
    result.add_argument("--review", required=True, choices=("变过的", "全部"))
    result.add_argument("--cards", required=True, nargs="+", help="每个功能卡片 JSON 路径")
    result.add_argument("--output", required=True, help="决定清单 JSON 路径")
    return result


def _feature_tree(document):
    result = {}
    pending = [document]
    while pending:
        feature = pending.pop()
        if not isinstance(feature, dict):
            continue
        feature_id = feature.get("id")
        if isinstance(feature_id, str):
            result[feature_id] = feature
        children = feature.get("children")
        if isinstance(children, list):
            pending.extend(reversed(children))
    return result


def _card_errors(card, label):
    if not isinstance(card, dict):
        return [f"{label} 必须是对象"]
    problems = [f"{label} 缺少字段 {key}" for key in sorted(CARD_KEYS - card.keys())]
    problems.extend(f"{label} 有多余字段 {key}" for key in sorted(card.keys() - CARD_KEYS))
    if not isinstance(card.get("feature"), str) or not card["feature"].strip():
        problems.append(f"{label}.feature 必须是非空字符串")
    for field in ("used", "changed", "todo", "map_issues", "suggestion"):
        values = card.get(field)
        if not isinstance(values, list):
            problems.append(f"{label}.{field} 必须是字符串数组")
            continue
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{label}.{field}[{index}] 必须是非空字符串")
    unused = card.get("unused")
    if not isinstance(unused, list):
        problems.append(f"{label}.unused 必须是数组")
    else:
        for index, value in enumerate(unused):
            item_label = f"{label}.unused[{index}]"
            if not isinstance(value, dict):
                problems.append(f"{item_label} 必须是对象")
                continue
            problems.extend(
                f"{item_label} 缺少字段 {key}" for key in sorted(UNUSED_KEYS - value.keys())
            )
            problems.extend(
                f"{item_label} 有多余字段 {key}" for key in sorted(value.keys() - UNUSED_KEYS)
            )
            for field in ("what", "evidence", "risk"):
                if field in value and (not isinstance(value[field], str) or not value[field].strip()):
                    problems.append(f"{item_label}.{field} 必须是非空字符串")
            lines = value.get("lines")
            if type(lines) is not int or lines < 0:
                problems.append(f"{item_label}.lines 必须是非负整数")
    recommendation = card.get("recommended")
    if not isinstance(recommendation, str) or recommendation not in RECOMMENDATIONS:
        problems.append(f"{label}.recommended 必须是 照建议、留、不要这个功能 之一")
    elif recommendation == "照建议" and not card.get("suggestion"):
        problems.append(f"{label}.recommended 为 照建议 时 suggestion 不能为空")
    return problems


def _read_cards(paths):
    cards = []
    for path in paths:
        card_path = Path(path)
        if not card_path.is_file():
            raise FileNotFoundError(f"卡片文件不存在：{card_path}")
        try:
            cards.append(json.loads(
                card_path.read_text(encoding="utf-8"),
                object_pairs_hook=object_without_duplicate_keys,
            ))
        except (OSError, UnicodeError, ValueError) as error:
            raise InvalidCardJson(f"卡片不是合法 JSON：{card_path}：{error}") from error
    return cards


def _load_previous_features(repository, commit):
    if commit == "无":
        return {}
    result = subprocess.run(
        ["git", "-C", str(repository), "show", f"{commit}:repo-map/features.json"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"无法读取上次认可提交 {commit} 的功能清单：{result.stderr.strip()}")
    try:
        return _feature_tree(json.loads(
            result.stdout, object_pairs_hook=object_without_duplicate_keys,
        ))
    except ValueError as error:
        raise ValueError(f"上次认可提交 {commit} 的功能清单不是合法 JSON：{error}") from error


def _draft_lines(feature, previous_feature):
    lines = []
    why = feature["why"]
    why_prefix = "为什么（起草，等你拍板）" if why["source"] == "起草" else "为什么"
    lines.append(f"{why_prefix}：{why['text']}")
    if why["source"] == "起草":
        previous_why = previous_feature.get("why") if previous_feature else None
        if isinstance(previous_why, dict) and previous_why.get("text") != why["text"]:
            lines.append(f"原来：{previous_why['text']}")

    previous_items = {
        item["id"]: item
        for item in previous_feature.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    } if previous_feature else {}
    for item in feature["items"]:
        if item["status"] != "起草":
            continue
        text = f"起草条目 {item['id']}：{item['text']}"
        previous = previous_items.get(item["id"])
        if previous is not None and previous.get("text") != item["text"]:
            text += f"（原来：{previous['text']}）"
        lines.append(text)
    return lines


def _append_card_lines(lines, card):
    lines.extend(f"实际使用：{value}" for value in card["used"])
    for item in card["unused"]:
        lines.append(
            f"没人用：{item['what']}；依据：{item['evidence']}；"
            f"可减行数：{item['lines']}；风险：{item['risk']}"
        )
    lines.extend(f"变了什么：{value}" for value in card["changed"])
    lines.extend(f"TODO 大问题：{value}" for value in card["todo"])
    lines.extend(f"图上写偏：{value}" for value in card["map_issues"])
    lines.extend(f"照建议会做：{value}" for value in card["suggestion"])


def _decision_item(feature_id, feature, reason, commit, card, previous_features, data):
    lines = [f"本次纳入：{reason}", f"上次认可提交：{commit}"]
    lines.extend(_draft_lines(feature, previous_features.get(feature_id)))
    _append_card_lines(lines, card)
    options = list(COMMON_OPTIONS)
    if not data["features"][feature_id]["children"]:
        options.append(REMOVE_FEATURE)
    return {
        "id": feature_id,
        "title": feature["title"],
        "lines": lines,
        "options": options,
        "recommended": card["recommended"],
        "compare": None,
    }


def build_decisions(repository, data, values, planned, commits, cards):
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

    planned_ids = [feature_id for feature_id, _reason in planned]
    planned_set = set(planned_ids)
    for feature_id in sorted(set(by_id) - set(data["features"])):
        problems.append(f"未知功能卡片：{feature_id}")
    for feature_id in sorted(set(by_id) - planned_set):
        if feature_id in data["features"]:
            problems.append(f"卡片不在本次计划中：{feature_id}")
    for feature_id in planned_ids:
        if feature_id not in by_id:
            problems.append(f"计划功能缺少卡片：{feature_id}")
    for feature_id in planned_ids:
        card = by_id.get(feature_id)
        if not isinstance(card, dict):
            continue
        if (card.get("recommended") == REMOVE_FEATURE
                and data["features"][feature_id]["children"]):
            problems.append(f"有子功能的上级不能推荐「不要这个功能」：{feature_id}")
    if problems:
        return None, problems

    previous_features = {}
    for feature_id in planned_ids:
        commit = commits[feature_id]
        if commit != "无":
            previous = _load_previous_features(repository, commit)
            previous_features[feature_id] = previous.get(feature_id)
    items = [
        _decision_item(
            feature_id, values[feature_id], reason, commits[feature_id],
            by_id[feature_id], previous_features, data,
        )
        for feature_id, reason in planned
    ]
    decisions = {
        "title": "定期清扫功能审批",
        "intro": [
            "每个功能只选一项：照建议、留、按我写的改；没有子功能的功能另有「不要这个功能」。",
            "照建议：做卡片「照建议」列出的每件事。",
            "留：不删改代码、测试、文档或 TODO，也不做卡片建议；仍会确认本页列出的起草条目和理由。",
            "按我写的改：以「照建议」为底按备注修改；含义不明先问。",
            "不要这个功能：只给没有子功能的功能，删除该功能实现、测试、文档、TODO 和图上条目。",
            "前三项都会把本页列出的起草条目和功能理由照写的确认；点「留」也表示确认这些文字。",
        ],
        "groups": [{"title": "功能审批", "notes": [], "items": items}],
        "footnotes": [],
    }
    return validate_decisions(decisions), []


def _write_output(path, decisions):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(decisions, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output_path.parent,
        prefix=f".{output_path.name}.", suffix=".tmp", delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        temporary_file.write(content)
    temporary_path.replace(output_path)


def main(argv):
    options = parser().parse_args(argv)
    try:
        data, values, _records, _fingerprints, planned, commits = plan_data(
            options.repo, options.scope, options.review,
        )
        if not planned:
            raise ValueError("范围内没有变过的功能，不用出页")
        cards = _read_cards(options.cards)
        decisions, problems = build_decisions(
            options.repo, data, values, planned, commits, cards,
        )
        if problems:
            print("\n".join(problems))
            return 1
        _write_output(options.output, decisions)
    except ArgumentError as error:
        print(str(error), file=sys.stderr)
        return 2
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
