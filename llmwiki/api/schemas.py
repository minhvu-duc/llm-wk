from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    content: str
    content_type: str = "text/plain"
    declared_id: str | None = None
    source_uri: str | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateCollectionRequest(BaseModel):
    name: str
    config: dict[str, Any] | None = None


class ResolveRequest(BaseModel):
    resolution: str  # as_update | as_new | reject


class CreateKeyRequest(BaseModel):
    name: str
    allowed_collections: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    collections: list[str] | None = None
