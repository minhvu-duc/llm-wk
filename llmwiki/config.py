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
    # --- default-pipeline knobs (used by default_pipeline() when no explicit pipeline set) ---
    min_chars: int = 40
    denylist_patterns: list[str] = Field(default_factory=list)
    denylist_action: Literal["REJECT", "REVIEW"] = "REVIEW"
    knowledge_rubric: str = (
        "Keep durable facts, decisions/resolutions, reusable how-to steps, and persistent "
        "user/entity preferences or attributes. Drop greetings, small talk, scheduling chatter, "
        "and ephemeral or purely transactional exchanges with no lasting value."
    )
    # explicit composable pipeline; when None, default_pipeline(cfg) is used
    pipeline: list[dict] | None = None


def default_pipeline(cfg: "CollectionConfig") -> list[dict]:
    """The default pipeline reproduces the pre-framework behavior:
    validity (min_length + denylist + knowledge) -> dedup -> update (version only)."""
    return [
        {"gate": "validity", "rules": [
            {"type": "min_length", "params": {"min_chars": cfg.min_chars}},
            {"type": "regex_denylist", "params": {"patterns": cfg.denylist_patterns,
                                                  "action": cfg.denylist_action}},
            {"type": "knowledge_worthiness", "params": {"rubric": cfg.knowledge_rubric,
                                                        "on_uncertain": "REVIEW"}},
        ]},
        {"gate": "dedup", "rules": [
            {"type": "exact_duplicate"},
            {"type": "identity_match"},
            {"type": "semantic_duplicate", "params": {"threshold_high": cfg.high_threshold,
                                                      "gray_band": cfg.low_threshold,
                                                      "margin": cfg.margin}},
        ]},
        {"gate": "update", "rules": [{"type": "version_on_change"}]},
    ]


class Settings(BaseModel):
    data_dir: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_DATA", "./data"))
    db_path: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_DB", "./data/index.db"))
    provider: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_PROVIDER", "fake"))
    embed_model: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_EMBED_MODEL", "text-embedding-3-small"))
    adjudicate_model: str = Field(default_factory=lambda: os.environ.get("LLMWIKI_ADJ_MODEL", "gpt-4o-mini"))
