"""Pinned-version LikeC4 command-line wrapper."""
import json
from pathlib import Path
import subprocess
import tempfile


LIKEC4_VERSION = "1.60.0"


def run_likec4(arguments):
    command = ["npx", "-y", f"likec4@{LIKEC4_VERSION}", *arguments]
    return subprocess.run(command, capture_output=True, text=True)


def export_model(repo_map):
    # LikeC4 1.60.0 can export JSON for invalid syntax, so validate before exporting.
    source = str(Path(repo_map).resolve())
    validation = run_likec4(["validate", source])
    if validation.returncode != 0:
        details = (validation.stdout + validation.stderr).strip()
        raise ValueError(f"LikeC4 validate 失败：{details or '未提供错误输出'}")
    with tempfile.TemporaryDirectory(prefix="xy-repo-map-export-") as temporary:
        output = Path(temporary) / "model.json"
        result = run_likec4(["export", "json", "--pretty", "-o", str(output),
                             source])
        if result.returncode != 0:
            raise ValueError(result.stderr.strip() or result.stdout.strip() or "LikeC4 export json 失败")
        try:
            exported = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"无法读取 LikeC4 export json：{error}") from error
    if isinstance(exported, list):
        models = [item for item in exported if isinstance(item, dict) and "elements" in item]
        if not models:
            raise ValueError("LikeC4 export json 没有模型")
        return models[0]
    if not isinstance(exported, dict) or "elements" not in exported:
        raise ValueError("LikeC4 export json 格式不正确")
    return exported


def build_likec4(project, output):
    # Hash history keeps deep views openable when this static site is served below a path prefix.
    return run_likec4(["build", str(Path(project).resolve()), "-o", str(Path(output).resolve()),
                       "--base", "./", "--use-hash-history"])
