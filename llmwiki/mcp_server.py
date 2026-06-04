from __future__ import annotations
from llmwiki.auth.authz import authorize
from llmwiki.auth.base import AuthError, Principal
from llmwiki.coordinator import Coordinator
from llmwiki.models import IncomingDocument
from llmwiki.pipeline import IngestService
from llmwiki.providers.fake import FakeProvider
from llmwiki.storage import IndexStore


def build_core(data_dir: str, provider_name: str = "fake") -> dict:
    provider = FakeProvider()
    if provider_name != "fake":
        from llmwiki.providers.litellm_provider import LiteLLMProvider
        provider = LiteLLMProvider()
    index = IndexStore(f"{data_dir}/index.db")
    service = IngestService(index=index, content_root=f"{data_dir}/repos", provider=provider)
    return {"index": index, "service": service, "coordinator": Coordinator(service)}


def ingest_tool(core: dict, principal: Principal, collection: str, body: dict) -> dict:
    try:
        authorize(principal, collection, "ingest")
    except AuthError:
        return {"error": "forbidden"}
    doc = IncomingDocument(collection=collection, **body)
    rec = core["coordinator"].ingest(doc, principal_id=principal.id)
    return rec.model_dump(mode="json")


def get_decision_tool(core: dict, principal: Principal, decision_id: str) -> dict:
    rec = core["index"].get_decision(decision_id)
    return rec.model_dump(mode="json") if rec else {"error": "not_found"}


def list_pending_reviews_tool(core: dict, principal: Principal, collection: str) -> list:
    try:
        authorize(principal, collection, "reviewer")
    except AuthError:
        return [{"error": "forbidden"}]
    return [item.model_dump(mode="json") for item in core["index"].list_reviews(collection)]


def serve_mcp(data_dir: str, provider_name: str = "fake") -> None:  # pragma: no cover
    """Register the above as MCP tools and run the stdio server.
    Authn here uses a single configured admin principal (the MCP transport is
    trusted/local); collection scope is still enforced by the same authorize()."""
    from mcp.server.fastmcp import FastMCP
    core = build_core(data_dir, provider_name)
    mcp = FastMCP("llmwiki")
    admin = Principal(id="mcp", allowed_collections=[], roles=["admin"])

    @mcp.tool()
    def ingest_document(collection: str, content: str, declared_id: str | None = None) -> dict:
        return ingest_tool(core, admin, collection, {"content": content, "declared_id": declared_id})

    @mcp.tool()
    def get_decision(decision_id: str) -> dict:
        return get_decision_tool(core, admin, decision_id)

    @mcp.tool()
    def list_pending_reviews(collection: str) -> list:
        return list_pending_reviews_tool(core, admin, collection)

    mcp.run()
