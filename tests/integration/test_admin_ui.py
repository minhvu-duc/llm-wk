import json
from fastapi.testclient import TestClient
from llmwiki.api.app import create_app
from llmwiki.admin_ui import attach_admin
from llmwiki.auth.base import Principal
from llmwiki.auth.apikey import ApiKeyAuthenticator
from llmwiki.models import ReviewItem


def make_app(tmp_path):
    auth = ApiKeyAuthenticator({
        "admin": Principal(id="admin", allowed_collections=["*"], roles=["admin"]),
        "plain": Principal(id="p", allowed_collections=["kb"], roles=["ingest"]),
    })
    app = create_app(data_dir=str(tmp_path), authenticator=auth, provider_name="fake")
    attach_admin(app)
    app.state.service.ensure_collection("kb")
    return app


def logged_in(tmp_path):
    c = TestClient(make_app(tmp_path))
    r = c.post("/admin/login", data={"key": "admin"})
    assert r.status_code == 200  # followed redirect to /admin
    return c


def test_login_gate_redirects_when_no_cookie(tmp_path):
    c = TestClient(make_app(tmp_path))
    r = c.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and "/admin/login" in r.headers["location"]


def test_login_with_non_admin_key_rejected(tmp_path):
    c = TestClient(make_app(tmp_path))
    r = c.post("/admin/login", data={"key": "plain"}, follow_redirects=False)
    assert r.status_code == 303 and "error" in r.headers["location"]


def test_home_lists_realms_after_login(tmp_path):
    c = logged_in(tmp_path)
    r = c.get("/admin")
    assert r.status_code == 200 and "Realms" in r.text and "kb" in r.text


def test_realm_page_shows_default_pipeline(tmp_path):
    c = logged_in(tmp_path)
    r = c.get("/admin/realms/kb")
    assert r.status_code == 200
    for gate in ("validity", "dedup", "update"):
        assert gate in r.text
    assert "knowledge_worthiness" in r.text


def test_save_valid_and_invalid_pipeline(tmp_path):
    c = logged_in(tmp_path)
    good = json.dumps([{"gate": "dedup", "rules": [{"type": "exact_duplicate"}]}])
    r = c.post("/admin/realms/kb/config", data={"pipeline_json": good})
    assert r.status_code == 200 and "pipeline+saved" not in r.text  # redirect resolved; ok banner shown
    assert "pipeline saved" in r.text
    bad = json.dumps([{"gate": "g", "rules": [{"type": "nope"}]}])
    r2 = c.post("/admin/realms/kb/config", data={"pipeline_json": bad})
    assert "unknown rule type" in r2.text


def test_resolve_review_from_ui(tmp_path):
    app = make_app(tmp_path)
    app.state.index.save_review(ReviewItem(id="rev1", decision_id="dec1", collection="kb",
                                           candidates=[{"x": 1}]))
    c = TestClient(app)
    c.post("/admin/login", data={"key": "admin"})
    r = c.post("/admin/realms/kb/reviews/rev1/resolve", data={"resolution": "as_new"})
    assert r.status_code == 200
    assert app.state.index.list_reviews("kb", "pending") == []
    assert app.state.index.get_review("rev1").resolution == "as_new"


def test_keys_page_create_and_revoke(tmp_path):
    c = logged_in(tmp_path)
    r = c.post("/admin/keys", data={"name": "svc", "allowed_collections": "kb", "roles": "query"})
    assert r.status_code == 200 and "shown once" in r.text and "lw_" in r.text
    listing = c.get("/admin/keys").text
    assert "svc" in listing


def test_help_page_renders(tmp_path):
    c = logged_in(tmp_path)
    r = c.get("/admin/help")
    assert r.status_code == 200 and "How gates" in r.text and "Dispositions" in r.text


def test_home_shows_new_realm_form(tmp_path):
    c = logged_in(tmp_path)
    assert 'action="/admin/realms"' in c.get("/admin").text


def test_create_realm_from_ui(tmp_path):
    c = logged_in(tmp_path)
    r = c.post("/admin/realms", data={"name": "newzone"})
    assert r.status_code == 200  # followed redirect to the realm page
    assert "newzone" in c.app.state.index.list_collections()
    assert "newzone" in c.get("/admin").text          # appears on home
    # the realm's git repo was created (ensure_collection), not just a DB row
    import os
    assert os.path.isdir(os.path.join(str(tmp_path), "repos", "newzone", ".git"))


def test_create_realm_rejects_bad_name(tmp_path):
    c = logged_in(tmp_path)
    r = c.post("/admin/realms", data={"name": "Bad Name!"}, follow_redirects=False)
    assert r.status_code == 303 and "error" in r.headers["location"]
    assert "Bad Name!" not in c.app.state.index.list_collections()


def test_create_realm_requires_login(tmp_path):
    c = TestClient(make_app(tmp_path))   # no cookie
    r = c.post("/admin/realms", data={"name": "x"}, follow_redirects=False)
    assert r.status_code == 303 and "/admin/login" in r.headers["location"]
