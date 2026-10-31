"""Test-only relation data for browser rendering edge cases."""
import json
from pathlib import Path


def append_page_relation(site, relation):
    """Add a renderer-only case after LikeC4 has produced the browser fixture."""
    page_path = Path(site) / "index.html"
    html = page_path.read_text(encoding="utf-8")
    opening = '<script id="page-data" type="application/json">'
    prefix, marker, content = html.partition(opening)
    payload, closing, suffix = content.partition("</script>")
    if not marker or not closing:
        raise AssertionError("index.html 缺少 page-data")
    page_data = json.loads(payload)
    page_data["relations"].append(relation)
    encoded = json.dumps(page_data, ensure_ascii=False).replace("</script>", "<\\/script>")
    page_path.write_text(prefix + opening + encoded + closing + suffix, encoding="utf-8")


def append_fixture_self_relation(site):
    """LikeC4 rejects self-links, so add one to the rendered page fixture."""
    append_page_relation(site, {"source": "workspace.session.events",
                                "target": "workspace.session.events", "title": "自循环"})
