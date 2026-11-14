import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "xy-repo-map" / "repo_map.py"
LIBRARY = ROOT / "xy-repo-map"
_UNSET = object()


def why(source="原话", text="让功能容易使用。", quote="用户说过的话。", origin="测试契约"):
    if source == "确认":
        quote, origin = "", "本次回答"
    elif source == "起草":
        quote, origin = "", ""
    return {"text": text, "source": source, "quote": quote, "from": origin}


def feature(feature_id, directory, children=None, entries=None, source="原话",
            subrepositories=_UNSET, external_dependencies=_UNSET, items=_UNSET):
    result = {
        "id": feature_id,
        "title": feature_id.title(),
        "what": f"提供 {feature_id} 功能。",
        "why": why(source),
        "directory": directory,
        "entries": [] if entries is None else entries,
        "children": [] if children is None else children,
    }
    if subrepositories is not _UNSET:
        result["subrepositories"] = subrepositories
    if external_dependencies is not _UNSET:
        result["external_dependencies"] = external_dependencies
    result["items"] = [] if items is _UNSET else items
    return result


def make_repo(test_case, files, root_feature):
    temporary = tempfile.TemporaryDirectory()
    test_case.addCleanup(temporary.cleanup)
    repository = Path(temporary.name) / "sample-repo"
    return _make_repository(repository, files, root_feature)


def make_cleanup_repo(test_case):
    return make_repo(test_case, {
        "README.md": "Demo.\n",
        "alpha/main.py": "value = 1\n",
        "beta/main.py": "value = 2\n",
    }, feature(
        "root", "", subrepositories=[], external_dependencies=[],
        items=[{"id": "core", "text": "提供 root 功能。", "status": "确认"}],
        children=[
            feature("alpha", "alpha", items=[{
                "id": "core", "text": "提供 alpha 功能。", "status": "确认",
            }]),
            feature("beta", "beta", items=[{
                "id": "core", "text": "提供 beta 功能。", "status": "确认",
            }]),
        ],
    ))


def _make_repository(repository, files, root_feature):
    repository.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    feature_path = repository / "repo-map" / "features.json"
    feature_path.parent.mkdir(parents=True, exist_ok=True)
    feature_path.write_text(json.dumps(root_feature, ensure_ascii=False), encoding="utf-8")
    skip_path = feature_path.parent / "skip.json"
    if not skip_path.exists():
        skip_path.write_text("[]\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True)
    return repository


def make_workspace(test_case, a_dependencies=("sub-b", "missing-tool"), b_dependencies=()):
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
            feature(
                "root", "", subrepositories=[],
                external_dependencies=list(dependencies), children=[
                    feature("part", "lib"),
                ],
            ),
        )

    _make_repository(
        workspace,
        {
            ".gitignore": "/sub-a/\n/sub-b/\n",
            "README.md": "Workspace root.\n",
        },
        feature("root", "", subrepositories=["sub-a", "sub-b"],
                external_dependencies=[]),
    )
    return workspace


def build_map(test_case, repository):
    script = (
        "import json,sys; sys.path.insert(0,sys.argv[1]); "
        "from repo_map_lib.map_data import build_map; "
        "print(json.dumps(build_map(sys.argv[2]),ensure_ascii=False))"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(LIBRARY), str(repository)],
        capture_output=True, text=True, check=False,
    )
    test_case.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    return json.loads(result.stdout)
