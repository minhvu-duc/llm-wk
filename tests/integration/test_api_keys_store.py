from llmwiki.storage.index_store import IndexStore


def store(tmp_path):
    return IndexStore(str(tmp_path / "i.db"))


def test_create_and_lookup_by_hash(tmp_path):
    s = store(tmp_path)
    s.create_api_key("k1", "hash-abc", "svc-a", ["kb"], ["ingest", "query"], "2026-01-01T00:00:00")
    got = s.get_api_key_by_hash("hash-abc")
    assert got["id"] == "k1" and got["allowed_collections"] == ["kb"]
    assert got["roles"] == ["ingest", "query"]
    assert s.get_api_key_by_hash("nope") is None


def test_revoke_hides_key(tmp_path):
    s = store(tmp_path)
    s.create_api_key("k1", "hash-abc", "svc", ["*"], ["admin"], "2026-01-01T00:00:00")
    assert s.revoke_api_key("k1") is True
    assert s.get_api_key_by_hash("hash-abc") is None
    assert s.revoke_api_key("missing") is False


def test_list_keys_has_no_secrets(tmp_path):
    s = store(tmp_path)
    s.create_api_key("k1", "hash-abc", "svc", ["kb"], ["read"], "2026-01-01T00:00:00")
    keys = s.list_api_keys()
    assert len(keys) == 1
    assert "key_hash" not in keys[0] and keys[0]["id"] == "k1" and keys[0]["revoked"] is False


def test_list_collections(tmp_path):
    s = store(tmp_path)
    s.create_collection("kb"); s.create_collection("hr")
    assert s.list_collections() == ["hr", "kb"]
