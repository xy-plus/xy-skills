"""Serialize JSON safely for an HTML script element."""
import json


def json_for_html(value):
    """Return compact JSON that cannot close its containing script element."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
