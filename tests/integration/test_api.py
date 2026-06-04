from fastapi.testclient import TestClient
from llmwiki.api.app import create_app
from llmwiki.auth.base import Principal
from llmwiki.auth.apikey import ApiKeyAuthenticator


def client(tmp_path):
    auth = ApiKeyAuthenticator({"k-ingest": Principal(
        id="svc", allowed_collections=["kb"], roles=["ingest", "read", "reviewer"])})
    app = create_app(data_dir=str(tmp_path), authenticator=auth, provider_name="fake")
    c = TestClient(app)
    c.post("/v1/collections", json={"name": "kb"}, headers={"authorization": "Bearer k-ingest"})
    return c


H = {"authorization": "Bearer k-ingest"}


def test_healthz(tmp_path):
    assert client(tmp_path).get("/healthz").json()["status"] == "ok"


def test_ingest_new_then_duplicate(tmp_path):
    c = client(tmp_path)
    r1 = c.post("/v1/collections/kb/documents",
                json={"content": "hello world doc", "declared_id": "d1"}, headers=H)
    assert r1.status_code == 200 and r1.json()["outcome"] == "NEW"
    r2 = c.post("/v1/collections/kb/documents",
                json={"content": "hello world doc", "declared_id": "d1"}, headers=H)
    assert r2.json()["outcome"] == "DUPLICATE"


def test_ingest_requires_auth(tmp_path):
    c = client(tmp_path)
    r = c.post("/v1/collections/kb/documents", json={"content": "x"})
    assert r.status_code == 401


def test_ingest_denied_collection_is_403(tmp_path):
    c = client(tmp_path)
    c.post("/v1/collections", json={"name": "secret"}, headers=H)
    r = c.post("/v1/collections/secret/documents", json={"content": "x"}, headers=H)
    # principal allowed only in kb
    assert r.status_code == 403


def test_get_decision(tmp_path):
    c = client(tmp_path)
    rid = c.post("/v1/collections/kb/documents",
                 json={"content": "decide me", "declared_id": "d9"}, headers=H).json()["id"]
    g = c.get(f"/v1/decisions/{rid}", headers=H)
    assert g.status_code == 200 and g.json()["id"] == rid
