from datetime import datetime
import json
from pathlib import Path
import subprocess

from .git_repository import ArgumentError


PLATFORM_PYTHON = "python3"
PORTAL_SCRIPT = Path("/home/xy/web-portal/portal.py")
PUBLISH_MODES = ("当前", "清扫前", "清扫后")


def require_render_output_directory(output):
    """Require the two files produced by render or render-workspace."""
    directory = Path(output).expanduser().resolve()
    if not directory.is_dir():
        raise ArgumentError(f"--out 必须是 render 生成的目录：{directory}")
    for filename in ("index.html", "map-data.json"):
        if not (directory / filename).is_file():
            raise ArgumentError(f"发布目录缺少 {filename}：{directory}")
    return directory


def _site_name(map_path, as_mode):
    try:
        data = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArgumentError(f"无法读取 map-data.json：{error}") from error

    try:
        root = data["features"][data["root"]]
    except (KeyError, TypeError):
        raise ArgumentError("map-data.json 缺少根功能") from None
    try:
        directory_name = root["directory_name"]
    except (KeyError, TypeError):
        raise ArgumentError("map-data.json 根功能缺少 directory_name") from None
    if not isinstance(directory_name, str) or not directory_name.strip():
        raise ArgumentError("map-data.json 根功能缺少 directory_name")

    is_workspace = any("/" in feature_id for feature_id in data["features"])
    suffix = "-overview-map" if is_workspace else "-map"
    base_name = directory_name + suffix
    if as_mode == "当前":
        return base_name

    date = datetime.now().astimezone().strftime("%Y%m%d")
    ending = "before" if as_mode == "清扫前" else "after"
    return f"{base_name}-{date}-{ending}"


def publish_repository(output, as_mode):
    """Publish a render using its repository-derived site name and selected purpose."""
    directory = require_render_output_directory(output)
    name = _site_name(directory / "map-data.json", as_mode)
    result = subprocess.run(
        [PLATFORM_PYTHON, str(PORTAL_SCRIPT), "publish", name, str(directory)],
        check=False,
    )
    return result.returncode
