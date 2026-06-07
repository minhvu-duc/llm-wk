from fastapi.testclient import TestClient
from llmwiki.api.app import create_app
from llmwiki.auth.base import Principal
from llmwiki.auth.apikey import ApiKeyAuthenticator


def client(tmp_path):
    auth = ApiKeyAuthenticator({
        "admin": Principal(id="admin", allowed_collections=["*"], roles=["admin"]),
        "plain": Principal(id="p", allowed_collections=["kb"], roles=["ingest"]),
    })
    return TestClient(create_app(data_dir=str(tmp_path), authenticator=auth, provider_name="fake"))


A = {"authorization": "Bearer admin"}
P = {"authorization": "Bearer plain"}


def test_create_key_returns_secret_once_then_lists_without_it(tmp_path):
    c = client(tmp_path)
    r = c.post("/v1/keys", json={"name": "svc-a", "allowed_collections": ["kb"], "roles": ["ingest", "query"]},
               headers=A)
    assert r.status_code == 200
    body = r.json()
    assert body["key"].startswith("lw_") and body["id"].startswith("key_")
    listing = c.get("/v1/keys", headers=A).json()
    assert len(listing) == 1
    assert "key" not in listing[0] and "key_hash" not in listing[0]
    assert listing[0]["roles"] == ["ingest", "query"]


def test_key_endpoints_require_admin(tmp_path):
    c = client(tmp_path)
    assert c.post("/v1/keys", json={"name": "x"}, headers=P).status_code == 403
    assert c.get("/v1/keys", headers=P).status_code == 403


def test_revoke_key(tmp_path):
    c = client(tmp_path)
    kid = c.post("/v1/keys", json={"name": "svc"}, headers=A).json()["id"]
    assert c.delete(f"/v1/keys/{kid}", headers=A).status_code == 200
    assert c.delete("/v1/keys/missing", headers=A).status_code == 404


def test_created_key_actually_authenticates_via_stored_auth(tmp_path):
    # create via API (ApiKeyAuthenticator), then verify the stored key works with StoredAuthenticator
    from llmwiki.auth.stored import StoredAuthenticator
    c = client(tmp_path)
    raw = c.post("/v1/keys", json={"name": "svc", "allowed_collections": ["kb"], "roles": ["query"]},
                 headers=A).json()["key"]
    index = c.app.state.index
    principal = StoredAuthenticator(index).authenticate({"authorization": f"Bearer {raw}"})
    assert principal.roles == ["query"] and principal.allowed_collections == ["kb"]
