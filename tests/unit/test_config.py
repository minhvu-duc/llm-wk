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
    assert c.min_chars == 40
    assert c.denylist_patterns == []
    assert c.denylist_action == "REVIEW"
    assert "Keep durable facts" in c.knowledge_rubric
    assert c.pipeline is None


def test_default_pipeline_reproduces_current_gates():
    from llmwiki.config import default_pipeline
    cfg = CollectionConfig()
    pipe = cfg.pipeline or default_pipeline(cfg)
    gate_names = [g["gate"] for g in pipe]
    assert gate_names == ["validity", "dedup", "update"]
    dedup_types = [r["type"] for r in pipe[1]["rules"]]
    assert dedup_types == ["exact_duplicate", "identity_match", "semantic_duplicate"]
    # semantic_replace is NOT in the default pipeline (opt-in)
    assert all(r["type"] != "semantic_replace" for g in pipe for r in g["rules"])
