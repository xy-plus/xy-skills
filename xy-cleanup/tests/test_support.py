"""Small report builders shared by cleanup tests."""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "xy-repo-map"))

def model():
    return {
        "elements": {
            "sample": {"id": "sample", "kind": "node", "title": "Sample"},
            "sample.alpha": {"id": "sample.alpha", "kind": "node", "title": "Alpha"},
            "sample.beta": {"id": "sample.beta", "kind": "node", "title": "Beta"},
        },
        "relations": {
            "alpha-beta": {
                "source": {"model": "sample.alpha"},
                "target": {"model": "sample.beta"},
                "title": "uses",
            }
        },
    }


def finding(kind, decision, changes_behavior):
    return {
        "kind": kind,
        "target": "src/alpha.py:3",
        "problem": "图遗漏了调用关系。",
        "proposal": "补上调用关系。",
        "changes_behavior": changes_behavior,
        "decision": decision,
        "basis": {"source": "xy-iron-law 第 1 条", "text": "第一性原理"},
        "evidence": "src/alpha.py:3 调用了 beta。",
        "benefit": "图能展示真实依赖。",
        "cost": "需要重新核对仓图。",
    }


def node_entry(node, files, findings, checked):
    entry = {"node": node, "findings": findings, "checked": checked}
    if files is not None:
        entry["files"] = list(files)
        entry["entities"] = [
            {"entity": path, "verdict": "无问题", "reason": "核对了入口和实现"}
            for path in files
        ]
    return entry


def report(role, nodes, repo):
    return {"role": role, "repo": repo, "nodes": nodes}


def report_input(path, document):
    return {"path": path, "data": document}


def valid_reports(owners):
    node_names = ["sample.alpha", "sample.beta"]
    main_nodes = [node_entry(node, [path for path, owner in owners.items()
                                    if owner.element == node], [], "核对了节点与名下文件")
                  for node in node_names]
    razor_nodes = [node_entry(node, [path for path, owner in owners.items()
                                     if owner.element == node], [], "核对了节点与名下文件")
                   for node in node_names]
    whole_nodes = [node_entry(node, None, [], "核对了整图和节点") for node in node_names]
    return [
        report_input("main.json", report("主审", main_nodes, "/repo")),
        report_input("razor.json", report("剃刀", razor_nodes, "/repo")),
        report_input("whole.json", report("全图", whole_nodes, "/repo")),
    ]
