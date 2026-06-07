from llmwiki.cli import _seed_admin_key
from llmwiki.storage.index_store import IndexStore
from llmwiki.auth.stored import StoredAuthenticator, hash_key
from llmwiki.api.app import create_app
from fastapi.testclient import TestClient


def test_seed_admin_key_idempotent(tmp_path):
    idx = IndexStore(str(tmp_path / "i.db"))
    _seed_admin_key(idx, "secret")
    _seed_admin_key(idx, "secret")          # idempotent
    assert len(idx.list_api_keys()) == 1
    p = StoredAuthenticator(idx).authenticate({"authorization": "Bearer secret"})
    assert p.roles == ["admin"] and p.allowed_collections == ["*"]


def test_create_app_with_authenticator_factory_binds_to_index(tmp_path):
    app = create_app(data_dir=str(tmp_path), authenticator_factory=StoredAuthenticator,
                     provider_name="fake")
    # seed directly into the app's index, then authenticate through the app
    app.state.index.create_api_key("k1", hash_key("topsecret"), "svc", ["*"], ["admin"],
                                   "2026-01-01T00:00:00")
    c = TestClient(app)
    assert c.get("/v1/keys", headers={"authorization": "Bearer topsecret"}).status_code == 200
    assert c.get("/v1/keys", headers={"authorization": "Bearer wrong"}).status_code == 401
