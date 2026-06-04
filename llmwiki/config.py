from __future__ import annotations
import os
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


class Settings(BaseModel):
    data_dir: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_DATA", "./data"))
    db_path: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_DB", "./data/index.db"))
    provider: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_PROVIDER", "fake"))
    embed_model: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_EMBED_MODEL", "text-embedding-3-small"))
    adjudicate_model: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_ADJ_MODEL", "gpt-4o-mini"))
