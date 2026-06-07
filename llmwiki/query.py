from __future__ import annotations
from llmwiki.providers.base import Provider
from llmwiki.storage import IndexStore

_SNIPPET = 300


def _snippet(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= _SNIPPET else text[:_SNIPPET] + "…"


class QueryService:
    """Read side: similarity search over current active versions, in one zone or globally."""

    def __init__(self, index: IndexStore, provider: Provider):
        self.index = index
        self.provider = provider

    def query_zone(self, collection: str, query: str, top_k: int = 5) -> list[dict]:
        embedding = self.provider.embed(query)
        results = self.index.search(collection, embedding, top_k)
        return [self._shape(r) for r in results]

    def query_global(self, collections: list[str], query: str, top_k: int = 5) -> list[dict]:
        embedding = self.provider.embed(query)
        merged: list[dict] = []
        for c in collections:
            merged.extend(self.index.search(c, embedding, top_k))
        merged.sort(key=lambda x: x["score"], reverse=True)
        return [self._shape(r) for r in merged[:top_k]]

    def resolve_zones(self, allowed: list[str], requested: list[str] | None) -> list[str]:
        """Resolve the set of zones a global query may touch given a principal's allowance."""
        all_zones = self.index.list_collections()
        scope = all_zones if "*" in allowed else [c for c in allowed if c in all_zones]
        if requested is not None:
            scope = [c for c in scope if c in requested]
        return scope

    @staticmethod
    def _shape(r: dict) -> dict:
        return {"document_id": r["document_id"], "collection": r["collection"],
                "score": r["score"], "snippet": _snippet(r.get("content", "")),
                "wiki_path": r.get("wiki_path")}
