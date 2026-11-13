"""Load and match repository paths excluded from map and cleanup work."""
import json
from pathlib import Path


SKIP_LIST_PATH = "repo-map/skip.json"


def _safe_parts(path):
    if not path or path.startswith("/") or "\\" in path:
        return None
    relative_path = path[:-1] if path.endswith("/") else path
    parts = relative_path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return parts


def read_skip_paths(repository, tracked_paths):
    """Return the validated skip list for one repository."""
    skip_file = Path(repository) / SKIP_LIST_PATH
    if not skip_file.is_file():
        raise ValueError(
            f"缺少 {SKIP_LIST_PATH}；没有要跳过的路径就写 []"
        )
    try:
        values = json.loads(skip_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"无法读取 {SKIP_LIST_PATH}：{error}") from error
    if not isinstance(values, list):
        raise ValueError(f"{SKIP_LIST_PATH} 必须是数组")

    tracked = set(tracked_paths)
    checked = []
    seen = set()
    for index, path in enumerate(values):
        label = f"{SKIP_LIST_PATH}[{index}]"
        if not isinstance(path, str):
            raise ValueError(f"{label} 必须是字符串")
        parts = _safe_parts(path)
        if parts is None:
            raise ValueError(f"{label} 路径不安全：{path}")
        if path in seen:
            raise ValueError(f"{label} 路径不能重复：{path}")
        seen.add(path)
        exists = (
            any(tracked_path.startswith(path) for tracked_path in tracked)
            if path.endswith("/") else path in tracked
        )
        if not exists:
            raise ValueError(f"{label} 路径不存在或没有被跟踪文件：{path}")
        checked.append(path)
    return tuple(checked)


def is_skipped_path(path, skip_paths):
    """Return whether a tracked file matches a file path or directory prefix."""
    return any(
        path == skipped or (skipped.endswith("/") and path.startswith(skipped))
        for skipped in skip_paths
    )
