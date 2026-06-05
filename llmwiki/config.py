from __future__ import annotations
import os
from typing import Literal
from pydantic import BaseModel, Field


class CollectionConfig(BaseModel):
    low_threshold: float = 0.80
    high_threshold: float = 0.97
    margin: float = 0.02
    adjudication_enabled: bool = True
    self_consistency_n: int = 1
    max_bytes: int = 5_000_000
    allowed_content_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "text/markdown", "text/html"]
    )
    # --- quality-control gates ---
    quality_enabled: bool = True
    gate_order: list[str] = Field(default_factory=lambda: ["min_info", "denylist", "knowledge"])
    min_chars: int = 40
    denylist_patterns: list[str] = Field(default_factory=list)
    denylist_action: Literal["REJECT", "REVIEW"] = "REVIEW"
    knowledge_rubric: str = (
        "Keep durable facts, decisions/resolutions, reusable how-to steps, and persistent "
        "user/entity preferences or attributes. Drop greetings, small talk, scheduling chatter, "
        "and ephemeral or purely transactional exchanges with no lasting value."
    )


class Settings(BaseModel):
    data_dir: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_DATA", "./data"))
    db_path: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_DB", "./data/index.db"))
    provider: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_PROVIDER", "fake"))
    embed_model: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_EMBED_MODEL", "text-embedding-3-small"))
    adjudicate_model: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_ADJ_MODEL", "gpt-4o-mini"))
