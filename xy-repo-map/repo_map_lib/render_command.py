"""Write map data and its self-contained feature-tree page."""
import json
from pathlib import Path

from .git_repository import ArgumentError
from .map_data import build_map
from .render_page import render_page


def render_repository(repository, output):
    """Validate the repository and write a page to an empty output directory."""
    directory = Path(output).expanduser().resolve()
    if directory.exists() and not directory.is_dir():
        raise ArgumentError(f"--out 必须是目录：{directory}")
    if directory.is_dir() and any(directory.iterdir()):
        raise ArgumentError(f"--out 必须不存在或为空：{directory}")
    data = build_map(repository)

    page_data = {
        "root": data["root"],
        "features": {
            feature_id: {key: value for key, value in feature.items() if key != "files"}
            for feature_id, feature in data["features"].items()
        },
        "markers": data["markers"],
    }
    map_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    page_text = render_page(page_data)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "map-data.json").write_text(map_text, encoding="utf-8")
    (directory / "index.html").write_text(page_text, encoding="utf-8")
    return directory
