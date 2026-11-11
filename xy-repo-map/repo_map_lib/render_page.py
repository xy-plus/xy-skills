from pathlib import Path

from .embedded_json import json_for_html


TEMPLATE = Path(__file__).resolve().parent / "templates" / "feature-tree.html"


def render_page(data):
    """Read the standalone page template and insert file-free map data."""
    embedded = json_for_html(data)
    template = TEMPLATE.read_text(encoding="utf-8")
    return template.replace("__MAP_DATA__", embedded)
