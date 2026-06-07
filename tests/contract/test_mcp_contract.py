from llmwiki.mcp_server import build_core, ingest_tool, get_decision_tool, query_tool
from llmwiki.auth.base import Principal


def test_mcp_ingest_matches_core_outcomes(tmp_path):
    core = build_core(data_dir=str(tmp_path), provider_name="fake")
    core["service"].ensure_collection("kb")
    principal = Principal(id="svc", allowed_collections=["kb"], roles=["ingest", "read"])
    body = {"content": "The enterprise refund window is thirty days from the invoice date.",
            "declared_id": "d1"}
    r1 = ingest_tool(core, principal, "kb", body)
    assert r1["outcome"] == "NEW"
    r2 = ingest_tool(core, principal, "kb", body)
    assert r2["outcome"] == "DUPLICATE"
    dec = get_decision_tool(core, principal, r1["id"])
    assert dec["id"] == r1["id"]


def test_mcp_ingest_enforces_authz(tmp_path):
    core = build_core(data_dir=str(tmp_path), provider_name="fake")
    core["service"].ensure_collection("kb")
    principal = Principal(id="svc", allowed_collections=["other"], roles=["ingest"])
    r = ingest_tool(core, principal, "kb", {"content": "x"})
    assert r["error"] == "forbidden"


def test_mcp_query_tool_matches_core(tmp_path):
    core = build_core(data_dir=str(tmp_path), provider_name="fake")
    core["service"].ensure_collection("kb")
    admin = Principal(id="a", allowed_collections=["*"], roles=["admin"])
    ingest_tool(core, admin, "kb", {"content": "The enterprise refund window is thirty days.",
                                    "declared_id": "d1"})
    q = query_tool(core, admin, "kb", "refund window", top_k=3)
    assert q["query"] == "refund window" and len(q["results"]) >= 1
    # query permission enforced
    noq = Principal(id="n", allowed_collections=["kb"], roles=["ingest"])
    assert query_tool(core, noq, "kb", "x")["error"] == "forbidden"
