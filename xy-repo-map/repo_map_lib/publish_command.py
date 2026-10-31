"""Validate a rendered map's mode before delegating publication to the portal."""
import json
from pathlib import Path
import subprocess
import sys


PLATFORM_PYTHON = "python3"
PORTAL_SCRIPT = Path("/home/xy/web-portal/portal.py")


def _publication_directory(output, name):
    directory = Path(output).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"--out 必须是 render 生成的目录：{directory}")
    map_path = directory / "map-data.json"
    if not map_path.is_file():
        raise ValueError(f"发布目录缺少 map-data.json：{directory}")
    try:
        map_data = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"无法读取发布目录的 map-data.json：{error}") from error

    mode = map_data.get("mode") if isinstance(map_data, dict) else None
    if mode not in ("草稿", "正式"):
        raise ValueError("map-data.json 的 mode 必须是 草稿 或 正式")

    marker = directory / "DRAFT"
    if marker.exists() and not marker.is_file():
        raise ValueError(f"DRAFT 标记必须是文件：{marker}")
    is_draft = marker.is_file()
    if is_draft != (mode == "草稿"):
        raise ValueError("DRAFT 标记与 map-data.json 的 mode 不一致")
    if mode == "草稿" and "draft" not in name:
        raise ValueError("草稿站点名必须包含 draft")
    if mode == "正式" and "draft" in name:
        raise ValueError("正式站点名不能包含 draft")
    return directory


def publish_repository(output, name):
    directory = _publication_directory(output, name)
    result = subprocess.run(
        [str(PLATFORM_PYTHON), str(PORTAL_SCRIPT), "publish", name, str(directory)],
        capture_output=True, text=True, check=False,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode
