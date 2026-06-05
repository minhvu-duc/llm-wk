from fastapi.testclient import TestClient
from llmwiki.api.app import create_app
from llmwiki.auth.base import Principal
from llmwiki.auth.apikey import ApiKeyAuthenticator


def make_client(tmp_path):
    auth = ApiKeyAuthenticator({
        "admin": Principal(id="admin", allowed_collections=["kb"], roles=["admin"]),
        "writer": Principal(id="w", allowed_collections=["kb"], roles=["ingest", "read"]),
    })
    app = create_app(data_dir=str(tmp_path), authenticator=auth, provider_name="fake")
    c = TestClient(app)
    c.post("/v1/collections", json={"name": "kb"}, headers={"authorization": "Bearer admin"})
    return c


A = {"authorization": "Bearer admin"}
W = {"authorization": "Bearer writer"}


def test_put_and_get_config(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/v1/collections/kb/config", json={"min_chars": 5, "quality_enabled": True}, headers=A)
    assert r.status_code == 200
    g = c.get("/v1/collections/kb/config", headers=A)
    assert g.status_code == 200 and g.json()["min_chars"] == 5


def test_put_config_requires_admin(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/v1/collections/kb/config", json={"min_chars": 5}, headers=W)
    assert r.status_code == 403


def test_invalid_regex_is_422(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/v1/collections/kb/config", json={"denylist_patterns": ["("]}, headers=A)
    assert r.status_code == 422


def test_per_collection_rules_change_outcome(tmp_path):
    c = make_client(tmp_path)
    # tighten min_chars so a medium sentence is rejected
    c.put("/v1/collections/kb/config", json={"min_chars": 1000}, headers=A)
    r = c.post("/v1/collections/kb/documents",
               json={"content": "The refund window is thirty days."}, headers=W)
    assert r.json()["outcome"] == "REJECTED"
    # loosen it -> same content is accepted
    c.put("/v1/collections/kb/config", json={"min_chars": 1}, headers=A)
    r2 = c.post("/v1/collections/kb/documents",
                json={"content": "The refund window is thirty days.", "declared_id": "p1"}, headers=W)
    assert r2.json()["outcome"] in ("NEW", "UPDATE")
