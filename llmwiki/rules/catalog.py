from __future__ import annotations
from llmwiki.rules.base import known_rules, get_rule

_CAT_ORDER = ["validity", "existence", "update_replace", "routing"]


def _field(name: str, prop: dict) -> dict:
    if "enum" in prop:
        return {"name": name, "type": "enum",
                "default": prop.get("default", prop["enum"][0]), "options": prop["enum"]}
    if "const" in prop:
        return {"name": name, "type": "enum",
                "default": prop.get("default", prop["const"]), "options": [prop["const"]]}
    t = prop.get("type")
    if t == "integer":
        return {"name": name, "type": "int", "default": prop.get("default", 0)}
    if t == "number":
        return {"name": name, "type": "float", "default": prop.get("default", 0.0)}
    if t == "boolean":
        return {"name": name, "type": "bool", "default": prop.get("default", False)}
    if t == "array":
        return {"name": name, "type": "list", "default": prop.get("default", [])}
    return {"name": name, "type": "str", "default": prop.get("default", "")}


def rule_catalog() -> list[dict]:
    rules = [get_rule(r) for r in known_rules()]
    rules.sort(key=lambda r: (_CAT_ORDER.index(r.category) if r.category in _CAT_ORDER else 99, r.id))
    out = []
    for r in rules:
        props = r.Params.model_json_schema().get("properties", {})
        out.append({"id": r.id, "category": r.category, "kind": r.kind,
                    "params": [_field(n, p) for n, p in props.items()]})
    return out
