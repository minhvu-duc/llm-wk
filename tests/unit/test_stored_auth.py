import pytest
from llmwiki.auth.stored import StoredAuthenticator, generate_key, hash_key
from llmwiki.auth.base import AuthError, Principal
from llmwiki.auth.authz import authorize
from llmwiki.storage.index_store import IndexStore


def test_generate_and_hash_are_stable():
    k = generate_key()
    assert k.startswith("lw_") and len(k) > 10
    assert hash_key("abc") == hash_key("abc") != hash_key("abd")


def test_stored_authenticator_resolves_principal(tmp_path):
    idx = IndexStore(str(tmp_path / "i.db"))
    raw = generate_key()
    idx.create_api_key("k1", hash_key(raw), "svc", ["kb"], ["ingest", "query"], "2026-01-01T00:00:00")
    auth = StoredAuthenticator(idx)
    p = auth.authenticate({"authorization": f"Bearer {raw}"})
    assert p.id == "k1" and "query" in p.roles


def test_stored_authenticator_rejects_unknown_and_revoked(tmp_path):
    idx = IndexStore(str(tmp_path / "i.db"))
    raw = generate_key()
    idx.create_api_key("k1", hash_key(raw), "svc", ["kb"], ["ingest"], "2026-01-01T00:00:00")
    auth = StoredAuthenticator(idx)
    with pytest.raises(AuthError):
        auth.authenticate({"authorization": "Bearer lw_deadbeef"})
    idx.revoke_api_key("k1")
    with pytest.raises(AuthError):
        auth.authenticate({"authorization": f"Bearer {raw}"})


def test_wildcard_allows_any_collection():
    p = Principal(id="svc", allowed_collections=["*"], roles=["query"])
    authorize(p, "anything", "query")  # no raise
    with pytest.raises(AuthError):
        authorize(p, "anything", "ingest")  # role still enforced
