"""Canonical function structure used for approvals and model comparisons."""
import hashlib
import json


def structure_from_export(model):
    """Return only element and relationship fields that need user approval."""
    elements = []
    for element_id, element in model.get("elements", {}).items():
        parent = element_id.rpartition(".")[0] or None
        description = element.get("description", "")
        if isinstance(description, dict):
            description = description.get("txt", "")
        elements.append({
            "id": element_id,
            "kind": element.get("kind", ""),
            "title": element.get("title", ""),
            "description": description,
            "parent": parent,
        })
    elements.sort(key=lambda item: item["id"])

    relationships = []
    for relation in model.get("relations", {}).values():
        source = relation.get("source", {})
        target = relation.get("target", {})
        if isinstance(source, dict):
            source = source.get("model", "")
        if isinstance(target, dict):
            target = target.get("model", "")
        relationships.append({
            "source": source,
            "target": target,
            "title": relation.get("title", ""),
        })
    relationships.sort(key=lambda item: (item["source"], item["target"], item["title"]))
    return {"elements": elements, "relationships": relationships}


def structure_digest(model):
    """Hash normalized function structure; links and source formatting are excluded."""
    canonical = json.dumps(
        structure_from_export(model),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
