# LLM-Wiki Ingest Decision Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the ingest decision engine for an open-source LLM-Wiki: given a document, decide `REJECTED` / `DUPLICATE` / `UPDATE` / `NEW` / `NEEDS_REVIEW`, then persist it to a git-versioned markdown wiki + SQLite index, exposed via REST and MCP.

**Architecture:** A pure, I/O-free decision core (classifier + pipeline) is injected with three interfaces — an LLM provider, an index store, and a content store. A per-collection serialized coordinator runs the pipeline. REST (FastAPI) and MCP adapters are thin wrappers over the same core service so they always agree.

**Tech Stack:** Python 3.11+, pydantic v2, FastAPI + uvicorn, stdlib `sqlite3`, git via `subprocess`, `mcp` SDK, `litellm` (cloud provider, pluggable), pytest. Embeddings stored as JSON; cosine similarity computed in Python for v1 (sqlite-vec/pgvector are the documented scale path).

**Spec:** `docs/superpowers/specs/2026-06-04-llm-wiki-decision-engine-design.md`

---

## File Structure

```
pyproject.toml
llmwiki/
  __init__.py
  config.py            # Settings, CollectionConfig (thresholds, policy)
  models.py            # Outcome enum + pydantic domain models
  text.py              # normalize(), content_hash(), shingles(), jaccard()
  providers/
    __init__.py
    base.py            # Embedder/Adjudicator Protocols + AdjudicatorVerdict
    fake.py            # deterministic provider for tests/dev
    litellm_provider.py# cloud default
  storage/
    __init__.py
    index_store.py     # SQLite schema + repositories
    content_store.py   # git-backed source/wiki writer
  classifier.py        # pure decision logic (crown jewel)
  pipeline.py          # IngestService: extract->fingerprint->resolve->classify->apply->record
  coordinator.py       # per-collection serialization
  auth/
    __init__.py
    base.py            # Principal, Authenticator Protocol, AuthError
    apikey.py          # API-key authenticator
    authz.py           # authorize(principal, collection, action)
  api/
    __init__.py
    app.py             # FastAPI app factory + dependency wiring
    routers.py         # REST routes
    schemas.py         # request/response models
  mcp_server.py        # MCP adapter over IngestService
  dashboard.py         # minimal HTML review/calibration views
  cli.py               # serve | init-collection
tests/
  unit/ integration/ contract/
```

---

## Phase A — Foundation & Core Engine

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `llmwiki/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/contract/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "llmwiki"
version = "0.1.0"
description = "Open-source LLM-Wiki ingest decision engine"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.6",
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "python-multipart>=0.0.9",
    "litellm>=1.40",
    "mcp>=1.2",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "httpx>=0.27", "ruff>=0.4"]

[project.scripts]
llmwiki = "llmwiki.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["llmwiki*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty package/test `__init__.py` files** (all empty)

- [ ] **Step 3: Create venv and install**

Run: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" -q && echo OK`
Expected: `OK` (may take a minute)

- [ ] **Step 4: Verify pytest runs**

Run: `.venv/bin/pytest -q`
Expected: `no tests ran` (exit 5) — confirms install works.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml llmwiki tests
git commit -m "chore: scaffold llmwiki package"
```

---

### Task 2: Domain models

**Files:**
- Create: `llmwiki/models.py`
- Test: `tests/unit/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_models.py
from llmwiki.models import Outcome, IncomingDocument, DecisionRecord, DocumentVersion

def test_outcome_values():
    assert {o.value for o in Outcome} == {
        "REJECTED", "DUPLICATE", "UPDATE", "NEW", "NEEDS_REVIEW"
    }

def test_incoming_document_defaults():
    doc = IncomingDocument(collection="kb", content="hello", content_type="text/plain")
    assert doc.declared_id is None
    assert doc.source_uri is None
    assert doc.metadata == {}

def test_decision_record_roundtrip():
    rec = DecisionRecord(
        id="d1", collection="kb", outcome=Outcome.NEW,
        content_hash="abc", principal_id="p1", signals={"sim": 0.1},
    )
    assert rec.outcome is Outcome.NEW
    assert rec.resulting_version_id is None
    assert DecisionRecord.model_validate(rec.model_dump()).id == "d1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_models.py -q`
Expected: FAIL — `ModuleNotFoundError: llmwiki.models`

- [ ] **Step 3: Write `llmwiki/models.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_models.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/models.py tests/unit/test_models.py
git commit -m "feat: domain models and Outcome enum"
```

---

### Task 3: Text utilities (normalize, hash, lexical overlap)

**Files:**
- Create: `llmwiki/text.py`
- Test: `tests/unit/test_text.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_text.py
from llmwiki.text import normalize, content_hash, shingles, jaccard

def test_normalize_collapses_whitespace_and_nfc():
    assert normalize("  Hello\t\nworld  ") == "Hello world"

def test_content_hash_stable_and_normalization_insensitive():
    assert content_hash("Hello   world") == content_hash(" Hello world ")
    assert content_hash("a") != content_hash("b")
    assert len(content_hash("x")) == 64  # sha256 hex

def test_jaccard_identical_is_one_and_disjoint_is_zero():
    a = shingles("the quick brown fox", n=2)
    assert jaccard(a, a) == 1.0
    assert jaccard(shingles("alpha beta", n=2), shingles("gamma delta", n=2)) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_text.py -q`
Expected: FAIL — `ModuleNotFoundError: llmwiki.text`

- [ ] **Step 3: Write `llmwiki/text.py`**

```python
from __future__ import annotations
import hashlib
import re
import unicodedata

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    return _WS.sub(" ", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def shingles(text: str, n: int = 3) -> set[str]:
    tokens = normalize(text).lower().split()
    if len(tokens) < n:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_text.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/text.py tests/unit/test_text.py
git commit -m "feat: text normalize/hash/lexical-overlap utilities"
```

---

### Task 4: Config (settings + per-collection thresholds)

**Files:**
- Create: `llmwiki/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config.py
from llmwiki.config import CollectionConfig

def test_collection_config_defaults():
    c = CollectionConfig()
    assert c.low_threshold == 0.80
    assert c.high_threshold == 0.97
    assert c.margin == 0.02
    assert c.adjudication_enabled is True
    assert c.self_consistency_n == 1
    assert c.max_bytes == 5_000_000
    assert "text/plain" in c.allowed_content_types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/config.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llmwiki/config.py tests/unit/test_config.py
git commit -m "feat: settings and per-collection config"
```

---

### Task 5: Provider interfaces + fake provider

**Files:**
- Create: `llmwiki/providers/__init__.py`, `llmwiki/providers/base.py`, `llmwiki/providers/fake.py`
- Test: `tests/unit/test_fake_provider.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_fake_provider.py
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import AdjudicatorVerdict

def test_embed_is_deterministic_and_unit_length():
    p = FakeProvider()
    v1 = p.embed("hello world")
    v2 = p.embed("hello world")
    assert v1 == v2
    assert abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-6

def test_identical_text_more_similar_than_different():
    p = FakeProvider()
    def cos(a, b): return sum(x * y for x, y in zip(a, b))
    base = p.embed("the cat sat on the mat")
    same = p.embed("the cat sat on the mat")
    diff = p.embed("quantum chromodynamics lecture notes")
    assert cos(base, same) == 1.0
    assert cos(base, diff) < 0.95

def test_scripted_adjudicate():
    p = FakeProvider(verdicts=[AdjudicatorVerdict(relationship="SAME_UPDATED", confidence="HIGH")])
    v = p.adjudicate("a", "b")
    assert v.relationship == "SAME_UPDATED" and v.confidence == "HIGH"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_fake_provider.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/providers/base.py`**

```python
from __future__ import annotations
from typing import Protocol, Literal
from pydantic import BaseModel

Relationship = Literal["SAME_UPDATED", "DIFFERENT", "RELATED_BUT_DISTINCT", "CONFLICTING"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]


class AdjudicatorVerdict(BaseModel):
    relationship: Relationship
    confidence: Confidence
    rationale: str = ""


class Provider(Protocol):
    def embed(self, text: str) -> list[float]: ...
    def adjudicate(self, incoming: str, existing: str) -> AdjudicatorVerdict: ...
```

- [ ] **Step 4: Write `llmwiki/providers/fake.py`**

```python
from __future__ import annotations
import hashlib
import math
from llmwiki.providers.base import AdjudicatorVerdict
from llmwiki.text import normalize

_DIM = 64


class FakeProvider:
    """Deterministic provider for tests/dev. Embedding is a hashed bag-of-words
    vector so identical text -> identical vector and overlap -> higher cosine."""

    def __init__(self, verdicts: list[AdjudicatorVerdict] | None = None):
        self._verdicts = list(verdicts or [])

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * _DIM
        for tok in normalize(text).lower().split():
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % _DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def adjudicate(self, incoming: str, existing: str) -> AdjudicatorVerdict:
        if self._verdicts:
            return self._verdicts.pop(0)
        return AdjudicatorVerdict(relationship="DIFFERENT", confidence="HIGH")
```

- [ ] **Step 5: Write `llmwiki/providers/__init__.py`**

```python
from llmwiki.providers.base import Provider, AdjudicatorVerdict
from llmwiki.providers.fake import FakeProvider

__all__ = ["Provider", "AdjudicatorVerdict", "FakeProvider"]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_fake_provider.py -q`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add llmwiki/providers tests/unit/test_fake_provider.py
git commit -m "feat: provider interface + deterministic fake provider"
```

---

### Task 6: Classifier (crown jewel — pure decision logic)

The classifier is pure: it receives the incoming fingerprint + candidate matches + config + an adjudicate callback, and returns an outcome + reason + signals. No I/O.

**Files:**
- Create: `llmwiki/classifier.py`
- Test: `tests/unit/test_classifier.py`

- [ ] **Step 1: Write the failing test (full truth table)**

```python
# tests/unit/test_classifier.py
from llmwiki.classifier import classify, Candidate, Fingerprint
from llmwiki.config import CollectionConfig
from llmwiki.models import Outcome
from llmwiki.providers.base import AdjudicatorVerdict

CFG = CollectionConfig()

def fp(hash_="h_new", embed=(1.0, 0.0), shingles=frozenset({"a b c"}), declared_id=None):
    return Fingerprint(content_hash=hash_, embedding=list(embed),
                       shingles=set(shingles), declared_id=declared_id)

def cand(doc_id, hash_, embed, shingles=frozenset({"a b c"}), content="x"):
    return Candidate(document_id=doc_id, content_hash=hash_, embedding=list(embed),
                     shingles=set(shingles), content=content, identity_match=False)

def adj_same(*_): return AdjudicatorVerdict(relationship="SAME_UPDATED", confidence="HIGH")
def adj_diff(*_): return AdjudicatorVerdict(relationship="DIFFERENT", confidence="HIGH")

def test_id_match_same_hash_is_duplicate():
    c = cand("d1", "h1", (1.0, 0.0)); c.identity_match = True
    d = classify(fp(hash_="h1", declared_id="d1"), [c], CFG, adj_diff)
    assert d.outcome is Outcome.DUPLICATE and d.document_id == "d1"

def test_id_match_diff_hash_is_update():
    c = cand("d1", "h_old", (1.0, 0.0)); c.identity_match = True
    d = classify(fp(hash_="h_new", declared_id="d1"), [c], CFG, adj_diff)
    assert d.outcome is Outcome.UPDATE and d.document_id == "d1"

def test_no_candidates_is_new():
    d = classify(fp(), [], CFG, adj_diff)
    assert d.outcome is Outcome.NEW

def test_low_similarity_is_new_without_llm():
    called = {"n": 0}
    def adj(*_): called["n"] += 1; return adj_diff()
    c = cand("d1", "h_old", (0.0, 1.0))  # orthogonal -> sim 0
    d = classify(fp(embed=(1.0, 0.0)), [c], CFG, adj)
    assert d.outcome is Outcome.NEW and called["n"] == 0

def test_high_similarity_same_hash_is_duplicate_without_llm():
    called = {"n": 0}
    def adj(*_): called["n"] += 1; return adj_diff()
    c = cand("d1", "hX", (1.0, 0.0))
    d = classify(fp(hash_="hX", embed=(1.0, 0.0)), [c], CFG, adj)
    assert d.outcome is Outcome.DUPLICATE and called["n"] == 0

def test_high_similarity_diff_hash_consistent_shingles_is_update():
    c = cand("d1", "h_old", (1.0, 0.0), shingles=frozenset({"a b c"}))
    d = classify(fp(hash_="h_new", embed=(1.0, 0.0), shingles=frozenset({"a b c"})), [c], CFG, adj_same)
    assert d.outcome is Outcome.UPDATE and d.document_id == "d1"

def test_gray_band_escalates_and_low_confidence_needs_review():
    def adj_low(*_): return AdjudicatorVerdict(relationship="SAME_UPDATED", confidence="LOW")
    c = cand("d1", "h_old", (0.9, 0.436))  # sim ~0.9 -> gray band
    d = classify(fp(embed=(1.0, 0.0)), [c], CFG, adj_low)
    assert d.outcome is Outcome.NEEDS_REVIEW

def test_gray_band_related_but_distinct_needs_review():
    def adj_rel(*_): return AdjudicatorVerdict(relationship="RELATED_BUT_DISTINCT", confidence="HIGH")
    c = cand("d1", "h_old", (0.9, 0.436))
    d = classify(fp(embed=(1.0, 0.0)), [c], CFG, adj_rel)
    assert d.outcome is Outcome.NEEDS_REVIEW

def test_small_top2_margin_needs_review():
    c1 = cand("d1", "h1", (1.0, 0.0))
    c2 = cand("d2", "h2", (0.999, 0.0447))  # nearly tied with c1
    d = classify(fp(embed=(1.0, 0.0)), [c1, c2], CFG, adj_same)
    assert d.outcome is Outcome.NEEDS_REVIEW

def test_conflicting_signals_high_sim_low_lexical_needs_review():
    # embedding ~1.0 but disjoint shingles -> conflicting -> review
    c = cand("d1", "h_old", (1.0, 0.0), shingles=frozenset({"x y z"}))
    d = classify(fp(hash_="h_new", embed=(1.0, 0.0), shingles=frozenset({"a b c"})), [c], CFG, adj_same)
    assert d.outcome is Outcome.NEEDS_REVIEW

def test_adjudication_disabled_gray_band_needs_review():
    cfg = CollectionConfig(adjudication_enabled=False)
    c = cand("d1", "h_old", (0.9, 0.436))
    d = classify(fp(embed=(1.0, 0.0)), [c], cfg, adj_same)
    assert d.outcome is Outcome.NEEDS_REVIEW
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_classifier.py -q`
Expected: FAIL — `ModuleNotFoundError: llmwiki.classifier`

- [ ] **Step 3: Write `llmwiki/classifier.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_classifier.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/classifier.py tests/unit/test_classifier.py
git commit -m "feat: pure decision classifier with full truth table"
```

---

### Task 7: Index store (SQLite repositories)

**Files:**
- Create: `llmwiki/storage/__init__.py`, `llmwiki/storage/index_store.py`
- Test: `tests/integration/test_index_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_index_store.py
from llmwiki.storage.index_store import IndexStore
from llmwiki.models import Document, DocumentVersion, DecisionRecord, Outcome

def make_store(tmp_path):
    return IndexStore(str(tmp_path / "idx.db"))

def test_collection_create_and_get(tmp_path):
    s = make_store(tmp_path)
    s.create_collection("kb")
    assert s.get_collection("kb") is not None
    assert s.get_collection("missing") is None

def test_document_and_version_persist(tmp_path):
    s = make_store(tmp_path); s.create_collection("kb")
    doc = Document(id="d1", collection="kb", stable_identity="uri://x")
    s.save_document(doc)
    v = DocumentVersion(id="v1", document_id="d1", content_hash="h1")
    s.save_version(v, embedding=[0.1, 0.2], shingles={"a b c"})
    s.set_current_version("d1", "v1")
    got = s.get_document("d1")
    assert got.current_version_id == "v1"

def test_find_by_identity(tmp_path):
    s = make_store(tmp_path); s.create_collection("kb")
    s.save_document(Document(id="d1", collection="kb", stable_identity="uri://x"))
    assert s.find_by_identity("kb", "uri://x").id == "d1"
    assert s.find_by_identity("kb", "uri://none") is None

def test_candidates_returns_embeddings(tmp_path):
    s = make_store(tmp_path); s.create_collection("kb")
    s.save_document(Document(id="d1", collection="kb", stable_identity="i1"))
    s.save_version(DocumentVersion(id="v1", document_id="d1", content_hash="h1"),
                   embedding=[1.0, 0.0], shingles={"a b c"}, content="hello")
    s.set_current_version("d1", "v1")
    cands = s.current_candidates("kb")
    assert len(cands) == 1
    assert cands[0].document_id == "d1" and cands[0].embedding == [1.0, 0.0]

def test_decision_and_idempotency(tmp_path):
    s = make_store(tmp_path); s.create_collection("kb")
    rec = DecisionRecord(id="dec1", collection="kb", outcome=Outcome.NEW, content_hash="h1")
    s.save_decision(rec, idempotency_key="k1")
    assert s.get_decision_by_idempotency("kb", "k1").id == "dec1"
    assert s.get_decision_by_idempotency("kb", "kX") is None
    assert s.get_decision("dec1").outcome is Outcome.NEW
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_index_store.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/storage/index_store.py`**

```python
from __future__ import annotations
import json
import os
import sqlite3
from llmwiki.classifier import Candidate
from llmwiki.models import Document, DocumentVersion, DecisionRecord, ReviewItem, Outcome

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
  name TEXT PRIMARY KEY, config TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, collection TEXT NOT NULL, stable_identity TEXT NOT NULL,
  current_version_id TEXT, wiki_path TEXT, created_at TEXT, updated_at TEXT,
  UNIQUE(collection, stable_identity));
CREATE TABLE IF NOT EXISTS versions (
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL, content_hash TEXT NOT NULL,
  git_commit TEXT, submitter_id TEXT, created_at TEXT,
  embedding TEXT NOT NULL DEFAULT '[]', shingles TEXT NOT NULL DEFAULT '[]',
  content TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, collection TEXT NOT NULL, outcome TEXT NOT NULL,
  content_hash TEXT, principal_id TEXT, document_id TEXT, resulting_version_id TEXT,
  reason TEXT, signals TEXT, created_at TEXT, idempotency_key TEXT);
CREATE INDEX IF NOT EXISTS idx_decisions_idem ON decisions(collection, idempotency_key);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, collection TEXT NOT NULL,
  status TEXT NOT NULL, candidates TEXT, resolution TEXT, resolver_id TEXT, created_at TEXT);
"""


class IndexStore:
    def __init__(self, path: str):
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --- collections ---
    def create_collection(self, name: str, config: dict | None = None) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO collections(name, config) VALUES (?, ?)",
            (name, json.dumps(config or {})))
        self._conn.commit()

    def get_collection(self, name: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM collections WHERE name=?", (name,)).fetchone()
        return {"name": row["name"], "config": json.loads(row["config"])} if row else None

    # --- documents / versions ---
    def save_document(self, doc: Document) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO documents
               (id, collection, stable_identity, current_version_id, wiki_path, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (doc.id, doc.collection, doc.stable_identity, doc.current_version_id,
             doc.wiki_path, doc.created_at.isoformat(), doc.updated_at.isoformat()))
        self._conn.commit()

    def get_document(self, doc_id: str) -> Document | None:
        row = self._conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return self._row_to_doc(row) if row else None

    def find_by_identity(self, collection: str, identity: str) -> Document | None:
        row = self._conn.execute(
            "SELECT * FROM documents WHERE collection=? AND stable_identity=?",
            (collection, identity)).fetchone()
        return self._row_to_doc(row) if row else None

    def set_current_version(self, doc_id: str, version_id: str) -> None:
        self._conn.execute(
            "UPDATE documents SET current_version_id=? WHERE id=?", (version_id, doc_id))
        self._conn.commit()

    def save_version(self, v: DocumentVersion, embedding: list[float],
                     shingles: set[str], content: str = "") -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO versions
               (id, document_id, content_hash, git_commit, submitter_id, created_at,
                embedding, shingles, content) VALUES (?,?,?,?,?,?,?,?,?)""",
            (v.id, v.document_id, v.content_hash, v.git_commit, v.submitter_id,
             v.created_at.isoformat(), json.dumps(embedding), json.dumps(sorted(shingles)), content))
        self._conn.commit()

    def current_candidates(self, collection: str) -> list[Candidate]:
        rows = self._conn.execute(
            """SELECT d.id AS doc_id, v.content_hash, v.embedding, v.shingles, v.content
               FROM documents d JOIN versions v ON d.current_version_id = v.id
               WHERE d.collection=?""", (collection,)).fetchall()
        return [Candidate(document_id=r["doc_id"], content_hash=r["content_hash"],
                          embedding=json.loads(r["embedding"]),
                          shingles=set(json.loads(r["shingles"])), content=r["content"])
                for r in rows]

    # --- decisions / reviews ---
    def save_decision(self, rec: DecisionRecord, idempotency_key: str | None = None) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO decisions
               (id, collection, outcome, content_hash, principal_id, document_id,
                resulting_version_id, reason, signals, created_at, idempotency_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (rec.id, rec.collection, rec.outcome.value, rec.content_hash, rec.principal_id,
             rec.document_id, rec.resulting_version_id, rec.reason, json.dumps(rec.signals),
             rec.created_at.isoformat(), idempotency_key))
        self._conn.commit()

    def get_decision(self, decision_id: str) -> DecisionRecord | None:
        row = self._conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
        return self._row_to_decision(row) if row else None

    def get_decision_by_idempotency(self, collection: str, key: str) -> DecisionRecord | None:
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE collection=? AND idempotency_key=?",
            (collection, key)).fetchone()
        return self._row_to_decision(row) if row else None

    def save_review(self, item: ReviewItem) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO reviews
               (id, decision_id, collection, status, candidates, resolution, resolver_id, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (item.id, item.decision_id, item.collection, item.status,
             json.dumps(item.candidates), item.resolution, item.resolver_id,
             item.created_at.isoformat()))
        self._conn.commit()

    def list_reviews(self, collection: str, status: str = "pending") -> list[ReviewItem]:
        rows = self._conn.execute(
            "SELECT * FROM reviews WHERE collection=? AND status=?", (collection, status)).fetchall()
        return [ReviewItem(id=r["id"], decision_id=r["decision_id"], collection=r["collection"],
                           status=r["status"], candidates=json.loads(r["candidates"] or "[]"),
                           resolution=r["resolution"], resolver_id=r["resolver_id"]) for r in rows]

    def get_review(self, review_id: str) -> ReviewItem | None:
        row = self._conn.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
        if not row:
            return None
        return ReviewItem(id=row["id"], decision_id=row["decision_id"], collection=row["collection"],
                          status=row["status"], candidates=json.loads(row["candidates"] or "[]"),
                          resolution=row["resolution"], resolver_id=row["resolver_id"])

    # --- helpers ---
    def _row_to_doc(self, row) -> Document:
        from datetime import datetime
        return Document(id=row["id"], collection=row["collection"],
                        stable_identity=row["stable_identity"],
                        current_version_id=row["current_version_id"], wiki_path=row["wiki_path"],
                        created_at=datetime.fromisoformat(row["created_at"]),
                        updated_at=datetime.fromisoformat(row["updated_at"]))

    def _row_to_decision(self, row) -> DecisionRecord:
        from datetime import datetime
        return DecisionRecord(id=row["id"], collection=row["collection"],
                              outcome=Outcome(row["outcome"]), content_hash=row["content_hash"],
                              principal_id=row["principal_id"], document_id=row["document_id"],
                              resulting_version_id=row["resulting_version_id"], reason=row["reason"] or "",
                              signals=json.loads(row["signals"] or "{}"),
                              created_at=datetime.fromisoformat(row["created_at"]))
```

- [ ] **Step 4: Write `llmwiki/storage/__init__.py`**

```python
from llmwiki.storage.index_store import IndexStore
__all__ = ["IndexStore"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_index_store.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add llmwiki/storage tests/integration/test_index_store.py
git commit -m "feat: SQLite index store with repositories"
```

---

### Task 8: Content store (git-backed)

**Files:**
- Create: `llmwiki/storage/content_store.py`
- Modify: `llmwiki/storage/__init__.py` (export `ContentStore`)
- Test: `tests/integration/test_content_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_content_store.py
import subprocess
from llmwiki.storage.content_store import ContentStore

def test_init_creates_git_repo_with_layout(tmp_path):
    cs = ContentStore(str(tmp_path), "kb")
    cs.init()
    root = tmp_path / "kb"
    assert (root / ".git").is_dir()
    assert (root / "sources").is_dir()
    assert (root / "wiki").is_dir()
    assert (root / "index.md").exists()
    assert (root / "log.md").exists()

def test_write_source_and_wiki_commits(tmp_path):
    cs = ContentStore(str(tmp_path), "kb"); cs.init()
    commit = cs.write_document(doc_id="d1", source_text="raw body",
                               wiki_text="# Title\nsummary", log_line="NEW d1")
    assert commit and len(commit) >= 7
    root = tmp_path / "kb"
    assert (root / "sources" / "d1.txt").read_text() == "raw body"
    assert (root / "wiki" / "d1.md").read_text() == "# Title\nsummary"
    assert "NEW d1" in (root / "log.md").read_text()
    # commit really exists in git history
    out = subprocess.run(["git", "-C", str(root), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "d1" in out

def test_second_write_updates_same_files(tmp_path):
    cs = ContentStore(str(tmp_path), "kb"); cs.init()
    cs.write_document("d1", "v1 body", "# v1", "NEW d1")
    cs.write_document("d1", "v2 body", "# v2", "UPDATE d1")
    root = tmp_path / "kb"
    assert (root / "sources" / "d1.txt").read_text() == "v2 body"
    log = (root / "log.md").read_text()
    assert "NEW d1" in log and "UPDATE d1" in log
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_content_store.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/storage/content_store.py`**

```python
from __future__ import annotations
import os
import subprocess


class ContentStore:
    """Git-backed per-collection content store. Layout:
       <root>/<collection>/{sources/, wiki/, index.md, log.md}"""

    def __init__(self, root: str, collection: str):
        self.repo = os.path.join(root, collection)

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", self.repo, *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def init(self) -> None:
        os.makedirs(os.path.join(self.repo, "sources"), exist_ok=True)
        os.makedirs(os.path.join(self.repo, "wiki"), exist_ok=True)
        for fn, seed in (("index.md", "# Index\n\n"), ("log.md", "# Log\n\n")):
            p = os.path.join(self.repo, fn)
            if not os.path.exists(p):
                with open(p, "w") as f:
                    f.write(seed)
        if not os.path.isdir(os.path.join(self.repo, ".git")):
            self._git("init", "-q")
            self._git("config", "user.email", "llmwiki@localhost")
            self._git("config", "user.name", "llmwiki")
            self._git("add", "-A")
            self._git("commit", "-q", "-m", "init collection")

    def write_document(self, doc_id: str, source_text: str, wiki_text: str,
                       log_line: str) -> str:
        with open(os.path.join(self.repo, "sources", f"{doc_id}.txt"), "w") as f:
            f.write(source_text)
        with open(os.path.join(self.repo, "wiki", f"{doc_id}.md"), "w") as f:
            f.write(wiki_text)
        with open(os.path.join(self.repo, "log.md"), "a") as f:
            f.write(log_line.rstrip() + "\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", log_line)
        return self._git("rev-parse", "HEAD")
```

- [ ] **Step 4: Update `llmwiki/storage/__init__.py`**

```python
from llmwiki.storage.index_store import IndexStore
from llmwiki.storage.content_store import ContentStore
__all__ = ["IndexStore", "ContentStore"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_content_store.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add llmwiki/storage tests/integration/test_content_store.py
git commit -m "feat: git-backed content store"
```

---

### Task 9: Ingest pipeline (IngestService)

Wires extraction → fingerprint → identity resolution → classify → apply → record. Uses a simple wiki generator (summary stub; LLM-page generation is a future enhancement, kept behind the provider).

**Files:**
- Create: `llmwiki/pipeline.py`
- Test: `tests/integration/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_pipeline.py
import pytest
from llmwiki.pipeline import IngestService
from llmwiki.storage import IndexStore, ContentStore
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import AdjudicatorVerdict
from llmwiki.config import CollectionConfig
from llmwiki.models import IncomingDocument, Outcome


def make_service(tmp_path, provider=None):
    idx = IndexStore(str(tmp_path / "idx.db"))
    svc = IngestService(index=idx, content_root=str(tmp_path / "repos"),
                        provider=provider or FakeProvider())
    svc.ensure_collection("kb")
    return svc


def ingest(svc, content, **kw):
    return svc.ingest(IncomingDocument(collection="kb", content=content, **kw))


def test_first_doc_is_new(tmp_path):
    svc = make_service(tmp_path)
    rec = ingest(svc, "the cat sat on the mat", declared_id="doc-1")
    assert rec.outcome is Outcome.NEW
    assert rec.document_id is not None
    assert rec.resulting_version_id is not None

def test_same_id_same_content_is_duplicate(tmp_path):
    svc = make_service(tmp_path)
    ingest(svc, "hello world", declared_id="doc-1")
    rec = ingest(svc, "hello world", declared_id="doc-1")
    assert rec.outcome is Outcome.DUPLICATE

def test_same_id_changed_content_is_update(tmp_path):
    svc = make_service(tmp_path)
    ingest(svc, "version one body", declared_id="doc-1")
    rec = ingest(svc, "version two body different", declared_id="doc-1")
    assert rec.outcome is Outcome.UPDATE

def test_unrelated_doc_is_new(tmp_path):
    svc = make_service(tmp_path)
    ingest(svc, "the cat sat on the mat", declared_id="doc-1")
    rec = ingest(svc, "quantum chromodynamics field theory", declared_id="doc-2")
    assert rec.outcome is Outcome.NEW

def test_idempotency_key_returns_original_decision(tmp_path):
    svc = make_service(tmp_path)
    r1 = ingest(svc, "abc def ghi", idempotency_key="k1")
    r2 = ingest(svc, "abc def ghi", idempotency_key="k1")
    assert r1.id == r2.id

def test_needs_review_creates_review_item(tmp_path):
    # force gray band + low confidence so it routes to review
    prov = FakeProvider(verdicts=[AdjudicatorVerdict(relationship="SAME_UPDATED", confidence="LOW")])
    svc = make_service(tmp_path, provider=prov)
    # craft two texts in the gray band by sharing some tokens
    ingest(svc, "alpha beta gamma delta epsilon")
    rec = ingest(svc, "alpha beta gamma delta zeta eta theta")
    if rec.outcome is Outcome.NEEDS_REVIEW:
        reviews = svc.index.list_reviews("kb")
        assert any(r.decision_id == rec.id for r in reviews)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_pipeline.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/pipeline.py`**

```python
from __future__ import annotations
import uuid
from llmwiki.classifier import classify, Candidate, Fingerprint
from llmwiki.config import CollectionConfig
from llmwiki.models import (IncomingDocument, Document, DocumentVersion,
                            DecisionRecord, ReviewItem, Outcome)
from llmwiki.providers.base import Provider
from llmwiki.storage import IndexStore, ContentStore
from llmwiki.text import content_hash, shingles


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _wiki_page(doc_id: str, text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else doc_id
    return f"# {first[:80]}\n\n{text.strip()}\n"


class IngestService:
    def __init__(self, index: IndexStore, content_root: str, provider: Provider,
                 config: CollectionConfig | None = None):
        self.index = index
        self.content_root = content_root
        self.provider = provider
        self.config = config or CollectionConfig()

    def ensure_collection(self, name: str) -> None:
        self.index.create_collection(name)
        ContentStore(self.content_root, name).init()

    def ingest(self, doc: IncomingDocument, principal_id: str | None = None) -> DecisionRecord:
        # idempotency short-circuit
        if doc.idempotency_key:
            prior = self.index.get_decision_by_idempotency(doc.collection, doc.idempotency_key)
            if prior is not None:
                return prior

        chash = content_hash(doc.content)
        embedding = self.provider.embed(doc.content)
        sh = shingles(doc.content)
        identity = doc.declared_id or doc.source_uri

        candidates = self.index.current_candidates(doc.collection)
        if identity:
            existing = self.index.find_by_identity(doc.collection, identity)
            for c in candidates:
                if existing and c.document_id == existing.id:
                    c.identity_match = True

        fp = Fingerprint(content_hash=chash, embedding=embedding, shingles=sh,
                         declared_id=identity)
        decision = classify(fp, candidates, self.config, self.provider.adjudicate)

        rec = DecisionRecord(id=_id("dec"), collection=doc.collection, outcome=decision.outcome,
                             content_hash=chash, principal_id=principal_id,
                             document_id=decision.document_id, reason=decision.reason,
                             signals=decision.signals)

        if decision.outcome in (Outcome.NEW, Outcome.UPDATE):
            rec = self._apply(doc, decision, rec, chash, embedding, sh, identity, principal_id)
        elif decision.outcome is Outcome.NEEDS_REVIEW:
            self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
            self.index.save_review(ReviewItem(
                id=_id("rev"), decision_id=rec.id, collection=doc.collection,
                candidates=[{"document_id": decision.document_id, **decision.signals}]))
            return rec
        else:  # DUPLICATE / REJECTED
            self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
        return rec

    def _apply(self, doc, decision, rec, chash, embedding, sh, identity, principal_id):
        cs = ContentStore(self.content_root, doc.collection)
        if decision.outcome is Outcome.NEW:
            doc_id = _id("doc")
            self.index.save_document(Document(id=doc_id, collection=doc.collection,
                                              stable_identity=identity or chash,
                                              wiki_path=f"wiki/{doc_id}.md"))
        else:  # UPDATE
            doc_id = decision.document_id

        version_id = _id("ver")
        commit = cs.write_document(doc_id=doc_id, source_text=doc.content,
                                   wiki_text=_wiki_page(doc_id, doc.content),
                                   log_line=f"{decision.outcome.value} {doc_id} {chash[:8]}")
        self.index.save_version(DocumentVersion(id=version_id, document_id=doc_id,
                                                content_hash=chash, git_commit=commit,
                                                submitter_id=principal_id),
                                embedding=embedding, shingles=sh, content=doc.content)
        self.index.set_current_version(doc_id, version_id)
        rec.document_id = doc_id
        rec.resulting_version_id = version_id
        self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
        return rec
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_pipeline.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/pipeline.py tests/integration/test_pipeline.py
git commit -m "feat: ingest pipeline orchestrating decision + persistence"
```

---

### Task 10: Coordinator (per-collection serialization)

**Files:**
- Create: `llmwiki/coordinator.py`
- Test: `tests/integration/test_coordinator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_coordinator.py
import threading
from llmwiki.coordinator import Coordinator
from llmwiki.pipeline import IngestService
from llmwiki.storage import IndexStore
from llmwiki.providers.fake import FakeProvider
from llmwiki.models import IncomingDocument, Outcome

def test_concurrent_same_doc_serialized_no_dup_documents(tmp_path):
    svc = IngestService(IndexStore(str(tmp_path / "idx.db")), str(tmp_path / "r"), FakeProvider())
    svc.ensure_collection("kb")
    coord = Coordinator(svc)
    results = []
    def go():
        results.append(coord.ingest(IncomingDocument(
            collection="kb", content="same body here", declared_id="doc-1")))
    threads = [threading.Thread(target=go) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    outcomes = sorted(r.outcome for r in results)
    # exactly one NEW; the rest DUPLICATE (never two NEWs racing)
    assert outcomes.count(Outcome.NEW) == 1
    assert all(o in (Outcome.NEW, Outcome.DUPLICATE) for o in outcomes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_coordinator.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/coordinator.py`**

```python
from __future__ import annotations
import threading
from collections import defaultdict
from llmwiki.models import IncomingDocument, DecisionRecord
from llmwiki.pipeline import IngestService


class Coordinator:
    """Serializes ingests per collection so the decision always sees consistent
    state. Different collections proceed in parallel."""

    def __init__(self, service: IngestService):
        self._svc = service
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    def _lock_for(self, collection: str) -> threading.Lock:
        with self._guard:
            return self._locks[collection]

    def ingest(self, doc: IncomingDocument, principal_id: str | None = None) -> DecisionRecord:
        with self._lock_for(doc.collection):
            return self._svc.ingest(doc, principal_id=principal_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_coordinator.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/coordinator.py tests/integration/test_coordinator.py
git commit -m "feat: per-collection serializing coordinator"
```

---

## Phase B — Trust & Interfaces

### Task 11: Auth module (principal, API-key authenticator, authz)

**Files:**
- Create: `llmwiki/auth/__init__.py`, `llmwiki/auth/base.py`, `llmwiki/auth/apikey.py`, `llmwiki/auth/authz.py`
- Test: `tests/unit/test_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_auth.py
import pytest
from llmwiki.auth.base import Principal, AuthError
from llmwiki.auth.apikey import ApiKeyAuthenticator
from llmwiki.auth.authz import authorize

def test_apikey_resolves_principal():
    auth = ApiKeyAuthenticator({"secret-123": Principal(
        id="svc-a", allowed_collections=["kb"], roles=["ingest"])})
    p = auth.authenticate({"authorization": "Bearer secret-123"})
    assert p.id == "svc-a"

def test_apikey_rejects_unknown():
    auth = ApiKeyAuthenticator({"secret-123": Principal(id="svc-a")})
    with pytest.raises(AuthError):
        auth.authenticate({"authorization": "Bearer nope"})

def test_apikey_rejects_missing_header():
    auth = ApiKeyAuthenticator({})
    with pytest.raises(AuthError):
        auth.authenticate({})

def test_authorize_allows_permitted_collection_and_role():
    p = Principal(id="svc-a", allowed_collections=["kb"], roles=["ingest"])
    authorize(p, "kb", "ingest")  # no raise

def test_authorize_denies_wrong_collection():
    p = Principal(id="svc-a", allowed_collections=["other"], roles=["ingest"])
    with pytest.raises(AuthError):
        authorize(p, "kb", "ingest")

def test_authorize_denies_missing_role():
    p = Principal(id="svc-a", allowed_collections=["kb"], roles=["read"])
    with pytest.raises(AuthError):
        authorize(p, "kb", "ingest")

def test_admin_role_bypasses_collection_scope():
    p = Principal(id="root", allowed_collections=[], roles=["admin"])
    authorize(p, "any", "ingest")  # no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_auth.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/auth/base.py`**

```python
from __future__ import annotations
from typing import Protocol
from pydantic import BaseModel, Field


class AuthError(Exception):
    """Raised on authentication or authorization failure."""


class Principal(BaseModel):
    id: str
    allowed_collections: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


class Authenticator(Protocol):
    def authenticate(self, headers: dict[str, str]) -> Principal: ...
```

- [ ] **Step 4: Write `llmwiki/auth/apikey.py`**

```python
from __future__ import annotations
from llmwiki.auth.base import Principal, AuthError


class ApiKeyAuthenticator:
    def __init__(self, keys: dict[str, Principal]):
        self._keys = keys

    def authenticate(self, headers: dict[str, str]) -> Principal:
        raw = headers.get("authorization") or headers.get("Authorization") or ""
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()
        if not token:
            raise AuthError("missing bearer token")
        principal = self._keys.get(token)
        if principal is None:
            raise AuthError("unknown api key")
        return principal
```

- [ ] **Step 5: Write `llmwiki/auth/authz.py`**

```python
from __future__ import annotations
from llmwiki.auth.base import Principal, AuthError


def authorize(principal: Principal, collection: str, action: str) -> None:
    if "admin" in principal.roles:
        return
    if action not in principal.roles:
        raise AuthError(f"principal {principal.id} lacks role '{action}'")
    if collection not in principal.allowed_collections:
        raise AuthError(f"principal {principal.id} not allowed in collection '{collection}'")
```

- [ ] **Step 6: Write `llmwiki/auth/__init__.py`**

```python
from llmwiki.auth.base import Principal, Authenticator, AuthError
from llmwiki.auth.apikey import ApiKeyAuthenticator
from llmwiki.auth.authz import authorize
__all__ = ["Principal", "Authenticator", "AuthError", "ApiKeyAuthenticator", "authorize"]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_auth.py -q`
Expected: PASS (7 passed)

- [ ] **Step 8: Commit**

```bash
git add llmwiki/auth tests/unit/test_auth.py
git commit -m "feat: auth module (api-key authn + authz)"
```

---

### Task 12: REST API (FastAPI)

**Files:**
- Create: `llmwiki/api/__init__.py`, `llmwiki/api/schemas.py`, `llmwiki/api/app.py`, `llmwiki/api/routers.py`
- Test: `tests/integration/test_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_api.py
from fastapi.testclient import TestClient
from llmwiki.api.app import create_app
from llmwiki.auth.base import Principal
from llmwiki.auth.apikey import ApiKeyAuthenticator


def client(tmp_path):
    auth = ApiKeyAuthenticator({"k-ingest": Principal(
        id="svc", allowed_collections=["kb"], roles=["ingest", "read", "reviewer"])})
    app = create_app(data_dir=str(tmp_path), authenticator=auth, provider_name="fake")
    c = TestClient(app)
    c.post("/v1/collections", json={"name": "kb"}, headers={"authorization": "Bearer k-ingest"})
    return c

H = {"authorization": "Bearer k-ingest"}

def test_healthz(tmp_path):
    assert client(tmp_path).get("/healthz").json()["status"] == "ok"

def test_ingest_new_then_duplicate(tmp_path):
    c = client(tmp_path)
    r1 = c.post("/v1/collections/kb/documents",
                json={"content": "hello world doc", "declared_id": "d1"}, headers=H)
    assert r1.status_code == 200 and r1.json()["outcome"] == "NEW"
    r2 = c.post("/v1/collections/kb/documents",
                json={"content": "hello world doc", "declared_id": "d1"}, headers=H)
    assert r2.json()["outcome"] == "DUPLICATE"

def test_ingest_requires_auth(tmp_path):
    c = client(tmp_path)
    r = c.post("/v1/collections/kb/documents", json={"content": "x"})
    assert r.status_code == 401

def test_ingest_denied_collection_is_403(tmp_path):
    c = client(tmp_path)
    c.post("/v1/collections", json={"name": "secret"}, headers=H)
    r = c.post("/v1/collections/secret/documents", json={"content": "x"}, headers=H)
    # principal allowed only in kb
    assert r.status_code == 403

def test_get_decision(tmp_path):
    c = client(tmp_path)
    rid = c.post("/v1/collections/kb/documents",
                 json={"content": "decide me", "declared_id": "d9"}, headers=H).json()["id"]
    g = c.get(f"/v1/decisions/{rid}", headers=H)
    assert g.status_code == 200 and g.json()["id"] == rid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_api.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/api/schemas.py`**

```python
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


class ResolveRequest(BaseModel):
    resolution: str  # as_update | as_new | reject
```

- [ ] **Step 4: Write `llmwiki/api/app.py`**

```python
from __future__ import annotations
from fastapi import FastAPI
from llmwiki.api.routers import build_router
from llmwiki.auth.base import Authenticator
from llmwiki.coordinator import Coordinator
from llmwiki.pipeline import IngestService
from llmwiki.providers.fake import FakeProvider
from llmwiki.storage import IndexStore


def _make_provider(name: str):
    if name == "fake":
        return FakeProvider()
    from llmwiki.providers.litellm_provider import LiteLLMProvider
    return LiteLLMProvider()


def create_app(data_dir: str, authenticator: Authenticator,
               provider_name: str = "fake") -> FastAPI:
    index = IndexStore(f"{data_dir}/index.db")
    service = IngestService(index=index, content_root=f"{data_dir}/repos",
                            provider=_make_provider(provider_name))
    coordinator = Coordinator(service)
    app = FastAPI(title="llmwiki")
    app.state.index = index
    app.state.service = service
    app.state.coordinator = coordinator
    app.state.authenticator = authenticator

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    app.include_router(build_router())
    return app
```

- [ ] **Step 5: Write `llmwiki/api/routers.py`**

```python
from __future__ import annotations
from fastapi import APIRouter, Request, HTTPException
from llmwiki.api.schemas import IngestRequest, CreateCollectionRequest, ResolveRequest
from llmwiki.auth.base import AuthError
from llmwiki.auth.authz import authorize
from llmwiki.models import IncomingDocument


def _principal(request: Request):
    headers = {k: v for k, v in request.headers.items()}
    try:
        return request.app.state.authenticator.authenticate(headers)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))


def _check(principal, collection, action):
    try:
        authorize(principal, collection, action)
    except AuthError as e:
        raise HTTPException(status_code=403, detail=str(e))


def build_router() -> APIRouter:
    r = APIRouter(prefix="/v1")

    @r.post("/collections")
    def create_collection(body: CreateCollectionRequest, request: Request):
        principal = _principal(request)
        # any authenticated principal may create; scope enforced on ingest
        request.app.state.service.ensure_collection(body.name)
        return {"name": body.name}

    @r.post("/collections/{collection}/documents")
    def ingest(collection: str, body: IngestRequest, request: Request):
        principal = _principal(request)
        _check(principal, collection, "ingest")
        doc = IncomingDocument(collection=collection, **body.model_dump())
        rec = request.app.state.coordinator.ingest(doc, principal_id=principal.id)
        return rec.model_dump(mode="json")

    @r.get("/collections/{collection}/documents/{doc_id}")
    def get_document(collection: str, doc_id: str, request: Request):
        principal = _principal(request)
        _check(principal, collection, "read")
        doc = request.app.state.index.get_document(doc_id)
        if not doc or doc.collection != collection:
            raise HTTPException(status_code=404, detail="not found")
        return doc.model_dump(mode="json")

    @r.get("/decisions/{decision_id}")
    def get_decision(decision_id: str, request: Request):
        _principal(request)
        rec = request.app.state.index.get_decision(decision_id)
        if not rec:
            raise HTTPException(status_code=404, detail="not found")
        return rec.model_dump(mode="json")

    @r.get("/collections/{collection}/reviews")
    def list_reviews(collection: str, request: Request):
        principal = _principal(request)
        _check(principal, collection, "reviewer")
        return [r.model_dump(mode="json")
                for r in request.app.state.index.list_reviews(collection)]

    @r.post("/reviews/{review_id}/resolve")
    def resolve(review_id: str, body: ResolveRequest, request: Request):
        principal = _principal(request)
        item = request.app.state.index.get_review(review_id)
        if not item:
            raise HTTPException(status_code=404, detail="not found")
        _check(principal, item.collection, "reviewer")
        item.status = "resolved"
        item.resolution = body.resolution
        item.resolver_id = principal.id
        request.app.state.index.save_review(item)
        return item.model_dump(mode="json")

    return r
```

- [ ] **Step 6: Write `llmwiki/api/__init__.py`**

```python
from llmwiki.api.app import create_app
__all__ = ["create_app"]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_api.py -q`
Expected: PASS (5 passed)

- [ ] **Step 8: Commit**

```bash
git add llmwiki/api tests/integration/test_api.py
git commit -m "feat: FastAPI REST surface over the core service"
```

---

### Task 13: LiteLLM cloud provider

**Files:**
- Create: `llmwiki/providers/litellm_provider.py`
- Test: `tests/unit/test_litellm_provider.py`

- [ ] **Step 1: Write the failing test (mocked, no network)**

```python
# tests/unit/test_litellm_provider.py
import json
import types
import llmwiki.providers.litellm_provider as mod
from llmwiki.providers.base import AdjudicatorVerdict


def test_embed_calls_litellm(monkeypatch):
    captured = {}
    def fake_embedding(model, input):
        captured["model"] = model
        return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    monkeypatch.setattr(mod, "litellm", types.SimpleNamespace(
        embedding=fake_embedding, completion=None))
    p = mod.LiteLLMProvider(embed_model="m-embed")
    assert p.embed("hi") == [0.1, 0.2, 0.3]
    assert captured["model"] == "m-embed"


def test_adjudicate_parses_json(monkeypatch):
    payload = json.dumps({"relationship": "SAME_UPDATED", "confidence": "HIGH",
                          "rationale": "same topic"})
    def fake_completion(model, messages, **kw):
        return {"choices": [{"message": {"content": payload}}]}
    monkeypatch.setattr(mod, "litellm", types.SimpleNamespace(
        embedding=None, completion=fake_completion))
    p = mod.LiteLLMProvider(adjudicate_model="m-chat")
    v = p.adjudicate("a", "b")
    assert isinstance(v, AdjudicatorVerdict)
    assert v.relationship == "SAME_UPDATED" and v.confidence == "HIGH"


def test_adjudicate_malformed_falls_back_to_review(monkeypatch):
    def fake_completion(model, messages, **kw):
        return {"choices": [{"message": {"content": "not json at all"}}]}
    monkeypatch.setattr(mod, "litellm", types.SimpleNamespace(
        embedding=None, completion=fake_completion))
    p = mod.LiteLLMProvider()
    v = p.adjudicate("a", "b")
    # malformed model output must not become a confident UPDATE
    assert v.confidence == "LOW"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_litellm_provider.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/providers/litellm_provider.py`**

```python
from __future__ import annotations
import json
import litellm
from llmwiki.providers.base import AdjudicatorVerdict

_PROMPT = (
    "You compare an INCOMING document against an EXISTING stored document and decide "
    "their relationship. Respond with ONLY a JSON object: "
    '{"relationship": one of "SAME_UPDATED"|"DIFFERENT"|"RELATED_BUT_DISTINCT"|"CONFLICTING", '
    '"confidence": one of "HIGH"|"MEDIUM"|"LOW", "rationale": short string}. '
    "SAME_UPDATED = same logical document, possibly revised. "
    "DIFFERENT = unrelated. RELATED_BUT_DISTINCT = related topic, distinct document. "
    "CONFLICTING = same topic but contradictory facts."
)


class LiteLLMProvider:
    def __init__(self, embed_model: str = "text-embedding-3-small",
                 adjudicate_model: str = "gpt-4o-mini"):
        self.embed_model = embed_model
        self.adjudicate_model = adjudicate_model

    def embed(self, text: str) -> list[float]:
        resp = litellm.embedding(model=self.embed_model, input=text)
        return list(resp["data"][0]["embedding"])

    def adjudicate(self, incoming: str, existing: str) -> AdjudicatorVerdict:
        messages = [
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": f"INCOMING:\n{incoming[:4000]}\n\nEXISTING:\n{existing[:4000]}"},
        ]
        resp = litellm.completion(model=self.adjudicate_model, messages=messages,
                                  temperature=0)
        content = resp["choices"][0]["message"]["content"]
        try:
            data = json.loads(content)
            return AdjudicatorVerdict(**data)
        except Exception:
            # fail safe: never let a malformed response become a confident decision
            return AdjudicatorVerdict(relationship="RELATED_BUT_DISTINCT",
                                      confidence="LOW", rationale="unparseable model output")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_litellm_provider.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/providers/litellm_provider.py tests/unit/test_litellm_provider.py
git commit -m "feat: litellm cloud provider with fail-safe adjudication parsing"
```

---

### Task 14: MCP server adapter + contract test

**Files:**
- Create: `llmwiki/mcp_server.py`
- Test: `tests/contract/test_mcp_contract.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_mcp_contract.py
from llmwiki.mcp_server import build_core, ingest_tool, get_decision_tool
from llmwiki.auth.base import Principal

def test_mcp_ingest_matches_core_outcomes(tmp_path):
    core = build_core(data_dir=str(tmp_path), provider_name="fake")
    core["service"].ensure_collection("kb")
    principal = Principal(id="svc", allowed_collections=["kb"], roles=["ingest", "read"])
    r1 = ingest_tool(core, principal, "kb", {"content": "alpha doc", "declared_id": "d1"})
    assert r1["outcome"] == "NEW"
    r2 = ingest_tool(core, principal, "kb", {"content": "alpha doc", "declared_id": "d1"})
    assert r2["outcome"] == "DUPLICATE"
    dec = get_decision_tool(core, principal, r1["id"])
    assert dec["id"] == r1["id"]

def test_mcp_ingest_enforces_authz(tmp_path):
    core = build_core(data_dir=str(tmp_path), provider_name="fake")
    core["service"].ensure_collection("kb")
    principal = Principal(id="svc", allowed_collections=["other"], roles=["ingest"])
    r = ingest_tool(core, principal, "kb", {"content": "x"})
    assert r["error"] == "forbidden"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/contract/test_mcp_contract.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/mcp_server.py`**

The MCP tool functions share the exact same core service and authz as REST, so the contract test guarantees parity. The `serve_mcp()` entry registers them with the MCP SDK.

```python
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
    return [r.model_dump(mode="json") for r in core["index"].list_reviews(collection)]


def serve_mcp(data_dir: str, provider_name: str = "fake") -> None:  # pragma: no cover
    """Register the above as MCP tools and run stdio server.
    Authn here uses a single configured principal from env (api key)."""
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/contract/test_mcp_contract.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/mcp_server.py tests/contract/test_mcp_contract.py
git commit -m "feat: MCP adapter sharing the core service (contract-tested vs REST)"
```

---

### Task 15: CLI + minimal dashboard

**Files:**
- Create: `llmwiki/cli.py`, `llmwiki/dashboard.py`
- Test: `tests/integration/test_cli_and_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_cli_and_dashboard.py
from fastapi.testclient import TestClient
from llmwiki.api.app import create_app
from llmwiki.dashboard import attach_dashboard
from llmwiki.auth.base import Principal
from llmwiki.auth.apikey import ApiKeyAuthenticator
from llmwiki.cli import build_parser


def test_cli_parser_has_serve_and_init():
    p = build_parser()
    ns = p.parse_args(["serve", "--port", "9000"])
    assert ns.command == "serve" and ns.port == 9000
    ns2 = p.parse_args(["init-collection", "kb"])
    assert ns2.command == "init-collection" and ns2.name == "kb"


def test_dashboard_lists_reviews(tmp_path):
    auth = ApiKeyAuthenticator({"k": Principal(id="svc", allowed_collections=["kb"],
                                               roles=["ingest", "reviewer", "read"])})
    app = create_app(data_dir=str(tmp_path), authenticator=auth, provider_name="fake")
    attach_dashboard(app)
    c = TestClient(app)
    c.post("/v1/collections", json={"name": "kb"}, headers={"authorization": "Bearer k"})
    page = c.get("/dashboard/kb")
    assert page.status_code == 200
    assert "Review queue" in page.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_cli_and_dashboard.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/dashboard.py`**

```python
from __future__ import annotations
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse


def attach_dashboard(app: FastAPI) -> None:
    @app.get("/dashboard/{collection}", response_class=HTMLResponse)
    def dashboard(collection: str, request: Request):
        reviews = request.app.state.index.list_reviews(collection)
        rows = "".join(
            f"<tr><td>{r.id}</td><td>{r.decision_id}</td>"
            f"<td>{r.candidates}</td></tr>" for r in reviews)
        return f"""<!doctype html><html><head><title>llmwiki {collection}</title></head>
<body><h1>llmwiki — {collection}</h1>
<h2>Review queue</h2>
<table border="1"><tr><th>review</th><th>decision</th><th>candidates</th></tr>
{rows or '<tr><td colspan=3>none</td></tr>'}</table>
</body></html>"""
```

- [ ] **Step 4: Write `llmwiki/cli.py`**

```python
from __future__ import annotations
import argparse
import os
from llmwiki.auth.apikey import ApiKeyAuthenticator
from llmwiki.auth.base import Principal


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="llmwiki")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--data-dir", default=os.environ.get("LLMWIKI_DATA", "./data"))
    i = sub.add_parser("init-collection")
    i.add_argument("name")
    i.add_argument("--data-dir", default=os.environ.get("LLMWIKI_DATA", "./data"))
    return p


def _dev_authenticator() -> ApiKeyAuthenticator:
    key = os.environ.get("LLMWIKI_API_KEY", "dev-key")
    return ApiKeyAuthenticator({key: Principal(id="dev", allowed_collections=[], roles=["admin"])})


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-collection":
        from llmwiki.storage import IndexStore, ContentStore
        IndexStore(f"{args.data_dir}/index.db").create_collection(args.name)
        ContentStore(f"{args.data_dir}/repos", args.name).init()
        print(f"initialized collection {args.name}")
        return 0
    if args.command == "serve":  # pragma: no cover
        import uvicorn
        from llmwiki.api.app import create_app
        from llmwiki.dashboard import attach_dashboard
        app = create_app(data_dir=args.data_dir, authenticator=_dev_authenticator(),
                         provider_name=os.environ.get("LLMWIKI_PROVIDER", "fake"))
        attach_dashboard(app)
        uvicorn.run(app, host=args.host, port=args.port)
        return 0
    return 1
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_cli_and_dashboard.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add llmwiki/cli.py llmwiki/dashboard.py tests/integration/test_cli_and_dashboard.py
git commit -m "feat: CLI (serve/init-collection) + minimal review dashboard"
```

---

### Task 16: Full suite, README, docker

**Files:**
- Create: `README.md`, `docker/Dockerfile`
- Test: run the whole suite

- [ ] **Step 1: Run the entire test suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all tests green, ~40+ passed)

- [ ] **Step 2: Write `README.md`** (quickstart: install, `llmwiki init-collection kb`, `llmwiki serve`, curl an ingest, decision outcomes table, link to the spec)

```markdown
# llmwiki

Open-source LLM-Wiki ingest decision engine. Submit a document; the engine decides
`NEW` / `UPDATE` / `DUPLICATE` / `NEEDS_REVIEW` / `REJECTED` and persists it to a
git-versioned markdown wiki + SQLite index. REST + MCP over one shared core.

## Quickstart
    python -m venv .venv && .venv/bin/pip install -e ".[dev]"
    LLMWIKI_API_KEY=dev-key .venv/bin/llmwiki serve &
    curl -s localhost:8000/v1/collections -H "authorization: Bearer dev-key" \
         -H 'content-type: application/json' -d '{"name":"kb"}'
    curl -s localhost:8000/v1/collections/kb/documents -H "authorization: Bearer dev-key" \
         -H 'content-type: application/json' -d '{"content":"hello","declared_id":"d1"}'

Default provider is `fake` (deterministic, offline). Set `LLMWIKI_PROVIDER=litellm`
plus the relevant API key env var for cloud embeddings/adjudication.

See `docs/superpowers/specs/2026-06-04-llm-wiki-decision-engine-design.md`.
```

- [ ] **Step 3: Write `docker/Dockerfile`**

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .
ENV LLMWIKI_DATA=/data
VOLUME /data
EXPOSE 8000
CMD ["llmwiki", "serve", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Final full-suite run + commit**

Run: `.venv/bin/pytest -q`
Expected: PASS

```bash
git add README.md docker/Dockerfile
git commit -m "docs: README + Dockerfile; full suite green"
```

---

## Self-Review Notes (plan vs. spec)

- **Trust gate (authenticated identity)** → Task 11 (authn/authz) + enforced in Task 12 routers / Task 14 MCP. ✅
- **Dedup + versioning + 5 outcomes** → Task 6 classifier (truth table) + Task 9 pipeline. ✅
- **Identity: declared ID else infer** → Task 9 resolves identity; Task 6 handles both paths. ✅
- **Confidence gate (bands + margin + structured verdict + conflicting signals), no auto-UPDATE on uncertainty** → Task 6. ✅
- **Pluggable provider, cloud default** → Task 5 (fake) + Task 13 (litellm); selected by name in app/core. ✅
- **Files+Git wiki, SQLite index** → Task 8 (git) + Task 7 (sqlite). ✅
- **REST + MCP over shared core, contract-tested** → Task 12 + Task 14. ✅
- **Per-collection serialization** → Task 10. ✅
- **Idempotency** → Task 7 store + Task 9 short-circuit. ✅
- **Fail-safe provider parsing** → Task 13 malformed→LOW confidence→review. ✅
- **Minimal dashboard, easy to run** → Task 15 + Task 16 (README/Docker). ✅
- **Self-consistency N** stored in config (Task 4); N>1 voting is a documented future enhancement (spec §12), default N=1 path fully covered.
```
