from fastapi.testclient import TestClient
from llmwiki.api.app import create_app
from llmwiki.auth.base import Principal
from llmwiki.auth.apikey import ApiKeyAuthenticator


def client(tmp_path):
    auth = ApiKeyAuthenticator({"k": Principal(id="k", allowed_collections=["*"], roles=["read"])})
    return TestClient(create_app(data_dir=str(tmp_path), authenticator=auth, provider_name="fake"))


def test_rule_types_lists_catalog(tmp_path):
    r = client(tmp_path).get("/v1/rule-types", headers={"authorization": "Bearer k"})
    assert r.status_code == 200
    ids = {rt["id"] for rt in r.json()["rule_types"]}
    assert {"min_length", "semantic_duplicate", "semantic_replace"} <= ids


def test_rule_types_requires_auth(tmp_path):
    assert client(tmp_path).get("/v1/rule-types").status_code == 401
