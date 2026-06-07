from llmwiki.rules.catalog import rule_catalog


def _by_id(cat):
    return {r["id"]: r for r in cat}


def test_catalog_covers_all_rules_with_shape():
    cat = rule_catalog()
    ids = _by_id(cat)
    for rid in ["min_length", "content_type", "regex_denylist", "knowledge_worthiness",
                "exact_duplicate", "identity_match", "semantic_duplicate",
                "version_on_change", "semantic_replace", "confidence_route", "accept"]:
        assert rid in ids
    assert ids["min_length"]["category"] == "validity"
    assert ids["semantic_duplicate"]["kind"] == "semantic"


def test_param_types_and_defaults():
    ids = _by_id(rule_catalog())
    ml = {p["name"]: p for p in ids["min_length"]["params"]}
    assert ml["min_chars"]["type"] == "int" and ml["min_chars"]["default"] == 40

    dl = {p["name"]: p for p in ids["regex_denylist"]["params"]}
    assert dl["patterns"]["type"] == "list"
    assert dl["action"]["type"] == "enum" and set(dl["action"]["options"]) == {"REJECT", "REVIEW"}

    sr = {p["name"]: p for p in ids["semantic_replace"]["params"]}
    assert sr["allow_unsignaled_replace"]["type"] == "bool"
    assert sr["threshold"]["type"] == "float"

    assert ids["exact_duplicate"]["params"] == []
