import pytest
from llmwiki.auth.base import Principal, AuthError
from llmwiki.auth.apikey import ApiKeyAuthenticator
from llmwiki.auth.authz import authorize


def test_apikey_resolves_principal():
    auth = ApiKeyAuthenticator({"secret-123": Principal(
        id="svc-a", allowed_collections=["kb"], roles=["ingest"])})
    p = auth.authenticate({"authorization": "Bearer secret-123"})
    assert p.id == "svc-a"


def test_apikey_rejects_unknown():
    auth = ApiKeyAuthenticator({"secret-123": Principal(id="svc-a")})
    with pytest.raises(AuthError):
        auth.authenticate({"authorization": "Bearer nope"})


def test_apikey_rejects_missing_header():
    auth = ApiKeyAuthenticator({})
    with pytest.raises(AuthError):
        auth.authenticate({})


def test_authorize_allows_permitted_collection_and_role():
    p = Principal(id="svc-a", allowed_collections=["kb"], roles=["ingest"])
    authorize(p, "kb", "ingest")  # no raise


def test_authorize_denies_wrong_collection():
    p = Principal(id="svc-a", allowed_collections=["other"], roles=["ingest"])
    with pytest.raises(AuthError):
        authorize(p, "kb", "ingest")


def test_authorize_denies_missing_role():
    p = Principal(id="svc-a", allowed_collections=["kb"], roles=["read"])
    with pytest.raises(AuthError):
        authorize(p, "kb", "ingest")


def test_admin_role_bypasses_collection_scope():
    p = Principal(id="root", allowed_collections=[], roles=["admin"])
    authorize(p, "any", "ingest")  # no raise
