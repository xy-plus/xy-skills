"""Render an offline feature-tree page by embedding map data into its template."""
import json
from pathlib import Path


TEMPLATE = Path(__file__).resolve().parent / "templates" / "feature-tree.html"


def render_page(data):
    """Read the standalone page template and insert file-free map data."""
    embedded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    embedded = embedded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = TEMPLATE.read_text(encoding="utf-8")
    return template.replace("__MAP_DATA__", embedded)
