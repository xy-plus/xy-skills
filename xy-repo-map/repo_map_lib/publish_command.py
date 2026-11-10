"""Validate a rendered feature map before delegating publication."""
from pathlib import Path
import subprocess
import sys

from .git_repository import ArgumentError


PLATFORM_PYTHON = "python3"
PORTAL_SCRIPT = Path("/home/xy/web-portal/portal.py")


def render_directory(output, name):
    """Require the two files produced by render and a nonempty site name."""
    if not isinstance(name, str) or not name.strip():
        raise ArgumentError("--name 必须是非空站点名")
    directory = Path(output).expanduser().resolve()
    if not directory.is_dir():
        raise ArgumentError(f"--out 必须是 render 生成的目录：{directory}")
    for filename in ("index.html", "map-data.json"):
        if not (directory / filename).is_file():
            raise ArgumentError(f"发布目录缺少 {filename}：{directory}")
    return directory


def publish_repository(output, name):
    """Call the platform publication script after validating a render output."""
    directory = render_directory(output, name)
    result = subprocess.run(
        [PLATFORM_PYTHON, str(PORTAL_SCRIPT), "publish", name, str(directory)],
        capture_output=True, text=True, check=False,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode
