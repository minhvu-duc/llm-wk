from llmwiki.config import CollectionConfig


def test_collection_config_defaults():
    c = CollectionConfig()
    assert c.low_threshold == 0.80
    assert c.high_threshold == 0.97
    assert c.margin == 0.02
    assert c.adjudication_enabled is True
    assert c.self_consistency_n == 1
    assert c.max_bytes == 5_000_000
    assert "text/plain" in c.allowed_content_types


def test_collection_config_quality_defaults():
    c = CollectionConfig()
    assert c.quality_enabled is True
    assert c.gate_order == ["min_info", "denylist", "knowledge"]
    assert c.min_chars == 40
    assert c.denylist_patterns == []
    assert c.denylist_action == "REVIEW"
    assert "Keep durable facts" in c.knowledge_rubric
