from fastapi.testclient import TestClient
from llmwiki.api.app import create_app
from llmwiki.auth.base import Principal
from llmwiki.auth.apikey import ApiKeyAuthenticator


def client(tmp_path):
    auth = ApiKeyAuthenticator({
        "admin": Principal(id="admin", allowed_collections=["*"], roles=["admin"]),
        "kbq": Principal(id="kbq", allowed_collections=["kb"], roles=["ingest", "query"]),
        "noq": Principal(id="noq", allowed_collections=["kb"], roles=["ingest"]),
    })
    c = TestClient(create_app(data_dir=str(tmp_path), authenticator=auth, provider_name="fake"))
    for name in ("kb", "hr"):
        c.post("/v1/collections", json={"name": name}, headers={"authorization": "Bearer admin"})
    # ingest with admin (wildcard) using a dedup-only pipeline so short docs aren't filtered
    for name in ("kb", "hr"):
        c.put(f"/v1/collections/{name}/config",
              json={"pipeline": [{"gate": "dedup", "rules": [{"type": "exact_duplicate"},
                                                             {"type": "semantic_duplicate"}]}]},
              headers={"authorization": "Bearer admin"})
    return c


ADM = {"authorization": "Bearer admin"}
KBQ = {"authorization": "Bearer kbq"}
NOQ = {"authorization": "Bearer noq"}


def test_query_zone_returns_results(tmp_path):
    c = client(tmp_path)
    c.post("/v1/collections/kb/documents", json={"content": "the cat sat on the warm mat", "declared_id": "a"}, headers=ADM)
    c.post("/v1/collections/kb/documents", json={"content": "quantum field theory notes", "declared_id": "b"}, headers=ADM)
    r = c.post("/v1/collections/kb/query", json={"query": "cat on a mat", "top_k": 2}, headers=KBQ)
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "cat on a mat"
    assert len(body["results"]) >= 1 and "cat" in body["results"][0]["snippet"]


def test_query_requires_query_role(tmp_path):
    c = client(tmp_path)
    assert c.post("/v1/collections/kb/query", json={"query": "x"}, headers=NOQ).status_code == 403


def test_query_denied_for_disallowed_zone(tmp_path):
    c = client(tmp_path)
    # kbq is only allowed in kb
    assert c.post("/v1/collections/hr/query", json={"query": "x"}, headers=KBQ).status_code == 403


def test_global_query_spans_allowed_zones(tmp_path):
    c = client(tmp_path)
    c.post("/v1/collections/kb/documents", json={"content": "the cat sat on the warm mat", "declared_id": "a"}, headers=ADM)
    c.post("/v1/collections/hr/documents", json={"content": "office cat pet policy", "declared_id": "b"}, headers=ADM)
    r = c.post("/v1/query", json={"query": "cat", "top_k": 5}, headers=ADM)
    assert r.status_code == 200
    body = r.json()
    assert set(body["zones"]) == {"kb", "hr"}
    assert {res["collection"] for res in body["results"]} <= {"kb", "hr"}
