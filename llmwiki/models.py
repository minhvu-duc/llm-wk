from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Outcome(str, Enum):
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"
    UPDATE = "UPDATE"
    NEW = "NEW"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REPLACE = "REPLACE"


class IncomingDocument(BaseModel):
    collection: str
    content: str
    content_type: str = "text/plain"
    declared_id: str | None = None       # submitter-provided stable ID/URI
    source_uri: str | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentVersion(BaseModel):
    id: str
    document_id: str
    content_hash: str
    git_commit: str | None = None
    submitter_id: str | None = None
    created_at: datetime = Field(default_factory=_now)


class Document(BaseModel):
    id: str
    collection: str
    stable_identity: str          # declared_id, source_uri, or derived
    current_version_id: str | None = None
    wiki_path: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    status: str = "active"          # active | replaced
    replaced_by: str | None = None
    replaces: str | None = None


class DecisionRecord(BaseModel):
    id: str
    collection: str
    outcome: Outcome
    content_hash: str
    principal_id: str | None = None
    document_id: str | None = None
    resulting_version_id: str | None = None
    reason: str = ""
    signals: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class ReviewItem(BaseModel):
    id: str
    decision_id: str
    collection: str
    status: str = "pending"        # pending | resolved
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    resolution: str | None = None  # as_update | as_new | reject
    resolver_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
