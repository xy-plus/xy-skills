import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "repository-care" / "xy-repo-map" / "repo_map.py"
LIBRARY = ROOT / "repository-care" / "xy-repo-map"
MISSING = object()


def map_command_arguments(command, repository):
    return ["python3", "-B", str(COMMAND), command, "--repo", str(repository)]


def run_map_command(command, repository, output):
    arguments = [*map_command_arguments(command, repository), "--out", str(output)]
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        check=False,
    )


sys.path.insert(0, str(ROOT / "repository-care"))
sys.path.insert(0, str(LIBRARY))
from repo_map_lib.map_data import build_map as build_repository_map  # noqa: E402


def feature(feature_id, directory, *, children, source, items):
    result = {
        "id": feature_id,
        "title": feature_id.title(),
        "what": f"提供 {feature_id} 功能。",
        "why": {
            "text": "让功能容易使用。",
            "source": source,
            "quote": "用户说过的话。" if source == "原话" else "",
            "from": "测试契约"
            if source == "原话"
            else "本次回答"
            if source == "确认"
            else "",
        },
        "directory": directory,
        "children": children,
    }
    if items is not MISSING:
        result["items"] = items
    return result


def root_feature(  # noqa: PLR0913 -- expose every required root field as a keyword
    feature_id,
    directory,
    *,
    children,
    source,
    items,
    subrepositories,
    external_dependencies,
):
    """Build a feature and keep root-only fields outside the generic factory."""
    result = feature(
        feature_id,
        directory,
        children=children,
        source=source,
        items=items,
    )
    if subrepositories is not MISSING:
        result["subrepositories"] = subrepositories
    if external_dependencies is not MISSING:
        result["external_dependencies"] = external_dependencies
    return result


def empty_root_feature():
    return root_feature(
        "root",
        "",
        children=[],
        source="原话",
        items=[],
        subrepositories=[],
        external_dependencies=[],
    )


def make_repo(test_case, files, feature_tree):
    temporary = tempfile.TemporaryDirectory()
    test_case.addCleanup(temporary.cleanup)
    repository = Path(temporary.name) / "sample-repo"
    return _make_repository(repository, files, feature_tree)


def make_cleanup_repo(test_case):
    return make_repo(
        test_case,
        {
            "README.md": "Demo.\n",
            "alpha/main.py": "value = 1\n",
            "beta/main.py": "value = 2\n",
        },
        root_feature(
            "root",
            "",
            source="原话",
            children=[
                feature(
                    "alpha",
                    "alpha",
                    source="原话",
                    children=[],
                    items=[
                        {
                            "id": "core",
                            "text": "提供 alpha 功能。",
                            "status": "确认",
                        }
                    ],
                ),
                feature(
                    "beta",
                    "beta",
                    source="原话",
                    children=[],
                    items=[
                        {
                            "id": "core",
                            "text": "提供 beta 功能。",
                            "status": "确认",
                        }
                    ],
                ),
            ],
            items=[{"id": "core", "text": "提供 root 功能。", "status": "确认"}],
            subrepositories=[],
            external_dependencies=[],
        ),
    )


def _make_repository(repository, files, feature_tree):
    repository.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    feature_path = repository / "repo-map" / "features.json"
    feature_path.parent.mkdir(parents=True, exist_ok=True)
    feature_path.write_text(
        json.dumps(feature_tree, ensure_ascii=False), encoding="utf-8"
    )
    skip_path = feature_path.parent / "skip.json"
    if not skip_path.exists():
        skip_path.write_text("[]\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True)
    return repository


def make_workspace(test_case, a_dependencies, b_dependencies):
    """Create a Git workspace whose ignored child repositories have independent maps."""
    temporary = tempfile.TemporaryDirectory()
    test_case.addCleanup(temporary.cleanup)
    workspace = Path(temporary.name) / "workspace"
    workspace.mkdir()

    for name, dependencies in (("sub-a", a_dependencies), ("sub-b", b_dependencies)):
        dependency_text = " ".join(dependencies) or "no external dependencies"
        _make_repository(
            workspace / name,
            {
                "README.md": f"Uses {dependency_text}.\n",
                "lib/main.py": "value = 1\n",
            },
            root_feature(
                "root",
                "",
                source="原话",
                children=[
                    feature("part", "lib", source="原话", children=[], items=[]),
                ],
                items=[],
                subrepositories=[],
                external_dependencies=list(dependencies),
            ),
        )

    _make_repository(
        workspace,
        {
            ".gitignore": "/sub-a/\n/sub-b/\n",
            "README.md": "Workspace root.\n",
        },
        root_feature(
            "root",
            "",
            source="原话",
            children=[],
            items=[],
            subrepositories=["sub-a", "sub-b"],
            external_dependencies=[],
        ),
    )
    return workspace


def build_map(repository):
    result = build_repository_map(repository)
    json.dumps(result, ensure_ascii=False)
    return result
