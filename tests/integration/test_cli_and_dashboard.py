from fastapi.testclient import TestClient
from llmwiki.api.app import create_app
from llmwiki.dashboard import attach_dashboard
from llmwiki.auth.base import Principal
from llmwiki.auth.apikey import ApiKeyAuthenticator
from llmwiki.cli import build_parser


def test_cli_parser_has_serve_and_init():
    p = build_parser()
    ns = p.parse_args(["serve", "--port", "9000"])
    assert ns.command == "serve" and ns.port == 9000
    ns2 = p.parse_args(["init-collection", "kb"])
    assert ns2.command == "init-collection" and ns2.name == "kb"


def test_dashboard_lists_reviews(tmp_path):
    auth = ApiKeyAuthenticator({"k": Principal(id="svc", allowed_collections=["kb"],
                                               roles=["ingest", "reviewer", "read"])})
    app = create_app(data_dir=str(tmp_path), authenticator=auth, provider_name="fake")
    attach_dashboard(app)
    c = TestClient(app)
    c.post("/v1/collections", json={"name": "kb"}, headers={"authorization": "Bearer k"})
    page = c.get("/dashboard/kb")
    assert page.status_code == 200
    assert "Review queue" in page.text
