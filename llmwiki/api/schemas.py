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
