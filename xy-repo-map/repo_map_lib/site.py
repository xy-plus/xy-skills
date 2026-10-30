"""Write an offline repository-map page and its machine-readable data file."""
import json
from pathlib import Path
import shutil

from .map_data import validate_map_data


PAGE_TEMPLATE = Path(__file__).with_name("templates") / "index.html"
ELK_DIRECTORY = Path(__file__).resolve().parents[1] / "third_party" / "elkjs-0.9.3"
PAGE_DATA_MARKER = "__PAGE_DATA_JSON__"
MODE_STATUS_MARKER = "__MODE_STATUS__"


def render_html(page_data):
    """Fill the local template with JSON safe inside an HTML script element."""
    template = PAGE_TEMPLATE.read_text(encoding="utf-8")
    if template.count(PAGE_DATA_MARKER) != 1 or template.count(MODE_STATUS_MARKER) != 1:
        raise ValueError("页面模板必须恰有一个数据位置和一个模式标记")
    encoded = json.dumps(page_data, ensure_ascii=False, separators=(",", ":"))
    status = "草稿·未经同意" if page_data["mode"] == "草稿" else ""
    return (template.replace(PAGE_DATA_MARKER, encoded.replace("</", "<\\/"))
            .replace(MODE_STATUS_MARKER, status))


def write_site(map_data, page_data, destination):
    """Write the JSON contract, HTML, local layout engine, and required license."""
    validate_map_data(map_data)
    if map_data["mode"] != page_data["mode"]:
        raise ValueError("页面和 map-data.json 的 mode 不一致")
    output = Path(destination)
    (output / "map-data.json").write_text(
        json.dumps(map_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "index.html").write_text(render_html(page_data), encoding="utf-8")
    shutil.copyfile(ELK_DIRECTORY / "elk.bundled.js", output / "elk.bundled.js")
    shutil.copyfile(ELK_DIRECTORY / "LICENSE.md", output / "LICENSE-elkjs-0.9.3.md")
    if map_data["mode"] == "草稿":
        (output / "DRAFT").write_text("草稿·未经同意\n", encoding="utf-8")
