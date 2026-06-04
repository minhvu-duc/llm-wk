from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Callable
from llmwiki.config import CollectionConfig
from llmwiki.models import Outcome
from llmwiki.providers.base import AdjudicatorVerdict
from llmwiki.text import jaccard

# Conflicting-signal guard: if embeddings say "very similar" but lexical
# overlap is near zero, the signals disagree -> human review.
_LEXICAL_FLOOR = 0.10


@dataclass
class Fingerprint:
    content_hash: str
    embedding: list[float]
    shingles: set[str] = field(default_factory=set)
    declared_id: str | None = None


@dataclass
class Candidate:
    document_id: str
    content_hash: str
    embedding: list[float]
    shingles: set[str] = field(default_factory=set)
    content: str = ""
    identity_match: bool = False


@dataclass
class Decision:
    outcome: Outcome
    document_id: str | None = None
    reason: str = ""
    signals: dict = field(default_factory=dict)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


AdjudicateFn = Callable[[str, str], AdjudicatorVerdict]


def classify(fp: Fingerprint, candidates: list[Candidate],
             cfg: CollectionConfig, adjudicate: AdjudicateFn) -> Decision:
    # 1. Direct identity match (submitter-provided ID/URI) is authoritative.
    id_match = next((c for c in candidates if c.identity_match), None)
    if id_match is not None:
        if id_match.content_hash == fp.content_hash:
            return Decision(Outcome.DUPLICATE, id_match.document_id, "identity + hash match",
                            {"path": "id"})
        return Decision(Outcome.UPDATE, id_match.document_id, "identity match, content changed",
                        {"path": "id"})

    if not candidates:
        return Decision(Outcome.NEW, None, "no candidates", {"path": "none"})

    # 2. Rank candidates by cosine similarity.
    scored = sorted(
        ((c, _cosine(fp.embedding, c.embedding)) for c in candidates),
        key=lambda t: t[1], reverse=True,
    )
    top, top_sim = scored[0]
    second_sim = scored[1][1] if len(scored) > 1 else 0.0
    margin = top_sim - second_sim
    signals = {"top_sim": round(top_sim, 4), "second_sim": round(second_sim, 4),
               "margin": round(margin, 4)}

    # Exact-hash duplicate regardless of band.
    if top.content_hash == fp.content_hash:
        return Decision(Outcome.DUPLICATE, top.document_id, "content hash match", signals)

    # 3. Similarity bands.
    if top_sim < cfg.low_threshold:
        return Decision(Outcome.NEW, None, "below low threshold", {**signals, "path": "band:new"})

    # Ambiguous which doc -> review, even at high absolute similarity.
    if len(scored) > 1 and margin < cfg.margin:
        return Decision(Outcome.NEEDS_REVIEW, None, "top-2 margin too small",
                        {**signals, "path": "margin"})

    lex = jaccard(fp.shingles, top.shingles)
    signals["lexical"] = round(lex, 4)

    if top_sim >= cfg.high_threshold:
        # Conflicting signals: high embedding sim but near-zero lexical overlap.
        if lex < _LEXICAL_FLOOR:
            return Decision(Outcome.NEEDS_REVIEW, None, "conflicting signals (high sim, low lexical)",
                            {**signals, "path": "conflict"})
        return Decision(Outcome.UPDATE, top.document_id, "above high threshold",
                        {**signals, "path": "band:update"})

    # 4. Gray band -> adjudicate (if enabled).
    if not cfg.adjudication_enabled:
        return Decision(Outcome.NEEDS_REVIEW, None, "gray band, adjudication disabled",
                        {**signals, "path": "graynoadj"})

    verdict = adjudicate(fp.content_hash, top.content)
    signals.update(relationship=verdict.relationship, confidence=verdict.confidence)

    if verdict.confidence != "HIGH":
        return Decision(Outcome.NEEDS_REVIEW, None, "low adjudication confidence",
                        {**signals, "path": "adj:lowconf"})
    if verdict.relationship in ("RELATED_BUT_DISTINCT", "CONFLICTING"):
        return Decision(Outcome.NEEDS_REVIEW, None, f"relationship {verdict.relationship}",
                        {**signals, "path": "adj:relation"})
    if lex < _LEXICAL_FLOOR:
        return Decision(Outcome.NEEDS_REVIEW, None, "conflicting signals after adjudication",
                        {**signals, "path": "adj:conflict"})
    if verdict.relationship == "SAME_UPDATED":
        return Decision(Outcome.UPDATE, top.document_id, "adjudicated same/updated",
                        {**signals, "path": "adj:update"})
    return Decision(Outcome.NEW, None, "adjudicated different", {**signals, "path": "adj:new"})
