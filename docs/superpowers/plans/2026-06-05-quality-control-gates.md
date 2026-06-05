# Quality-Control Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pluggable chain of quality-control gates that filters incoming content for knowledge-worthiness (before embedding) and lets operators set the rules per collection.

**Architecture:** A new `llmwiki/gates/` package defines a `Gate` protocol, built-in gates (deterministic + an LLM rubric gate), and a `GateChain` runner. `IngestService` loads per-collection config from the index store and runs the chain after the idempotency check and before fingerprint/embed; `REJECT → REJECTED`, `REVIEW → NEEDS_REVIEW`, `PASS →` existing dedup/version flow. Rules are settable via new collection-config REST endpoints.

**Tech Stack:** Python 3.11+, pydantic v2, stdlib `re`, FastAPI, pytest. Reuses the existing `Provider`, `IndexStore`, `DecisionRecord`, and review-queue machinery.

**Spec:** `docs/superpowers/specs/2026-06-05-quality-control-gates-design.md`

---

## File Structure

```
llmwiki/
  config.py            # MODIFY: add quality fields to CollectionConfig
  providers/base.py    # MODIFY: add KnowledgeVerdict + assess() to Provider
  providers/fake.py    # MODIFY: deterministic assess() + scripted verdicts
  providers/litellm_provider.py  # MODIFY: assess() with fail-safe parse
  gates/
    __init__.py        # CREATE: exports
    base.py            # CREATE: GateVerdict, GateContext, Gate protocol
    builtin.py         # CREATE: MinInfoGate, DenylistGate, KnowledgeWorthinessGate
    chain.py           # CREATE: GateChain, registry, build_chain()
  storage/index_store.py  # MODIFY: set_collection_config()
  pipeline.py          # MODIFY: _load_config() + run gate chain before embed
  api/schemas.py       # MODIFY: CreateCollectionRequest.config
  api/routers.py       # MODIFY: GET/PUT collection config endpoints
tests/
  unit/test_gates.py
  unit/test_knowledge_provider.py
  integration/test_pipeline_quality.py
  integration/test_collection_config.py
```

---

### Task 1: Add quality fields to CollectionConfig

**Files:**
- Modify: `llmwiki/config.py`
- Test: `tests/unit/test_config.py` (append)

- [ ] **Step 1: Write the failing test (append to existing file)**

```python
# append to tests/unit/test_config.py
def test_collection_config_quality_defaults():
    c = CollectionConfig()
    assert c.quality_enabled is True
    assert c.gate_order == ["min_info", "denylist", "knowledge"]
    assert c.min_chars == 40
    assert c.denylist_patterns == []
    assert c.denylist_action == "REVIEW"
    assert "Keep durable facts" in c.knowledge_rubric
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config.py::test_collection_config_quality_defaults -q`
Expected: FAIL — `AttributeError: ... quality_enabled`

- [ ] **Step 3: Add fields to `CollectionConfig`**

In `llmwiki/config.py`, change the import line and add fields to `CollectionConfig` (after `allowed_content_types`):

```python
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
```

(Leave the `Settings` class unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_config.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/config.py tests/unit/test_config.py
git commit -m "feat: add quality-gate config fields to CollectionConfig"
```

---

### Task 2: Add `assess` to the provider interface + fake provider

**Files:**
- Modify: `llmwiki/providers/base.py`, `llmwiki/providers/fake.py`
- Test: `tests/unit/test_knowledge_provider.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_knowledge_provider.py
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import KnowledgeVerdict

RUBRIC = "keep facts, drop greetings"


def test_fake_assess_greeting_is_not_knowledge():
    v = FakeProvider().assess("hi thanks", RUBRIC)
    assert isinstance(v, KnowledgeVerdict)
    assert v.is_knowledge is False and v.confidence == "HIGH"


def test_fake_assess_substantive_is_knowledge():
    v = FakeProvider().assess(
        "The refund window for enterprise customers is thirty days from invoice date.", RUBRIC)
    assert v.is_knowledge is True


def test_fake_assess_scripted_overrides_heuristic():
    scripted = KnowledgeVerdict(is_knowledge=False, category="x", confidence="MEDIUM")
    p = FakeProvider(assess_verdicts=[scripted])
    assert p.assess("a long substantive sentence about refunds and policies", RUBRIC).confidence == "MEDIUM"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_knowledge_provider.py -q`
Expected: FAIL — `ImportError: cannot import name 'KnowledgeVerdict'`

- [ ] **Step 3: Add `KnowledgeVerdict` + `assess` to `llmwiki/providers/base.py`**

Replace the file contents with:

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


class KnowledgeVerdict(BaseModel):
    is_knowledge: bool
    category: str = ""
    confidence: Confidence
    rationale: str = ""


class Provider(Protocol):
    def embed(self, text: str) -> list[float]: ...
    def adjudicate(self, incoming: str, existing: str) -> AdjudicatorVerdict: ...
    def assess(self, text: str, rubric: str) -> KnowledgeVerdict: ...
```

- [ ] **Step 4: Add `assess` to `llmwiki/providers/fake.py`**

Replace the file contents with:

```python
from __future__ import annotations
import hashlib
import math
from llmwiki.providers.base import AdjudicatorVerdict, KnowledgeVerdict
from llmwiki.text import normalize

_DIM = 64
_GREETINGS = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "got it", "bye", "yes", "no"}


class FakeProvider:
    """Deterministic provider for tests/dev. Embedding is a hashed bag-of-words
    vector so identical text -> identical vector and overlap -> higher cosine."""

    def __init__(self, verdicts: list[AdjudicatorVerdict] | None = None,
                 assess_verdicts: list[KnowledgeVerdict] | None = None):
        self._verdicts = list(verdicts or [])
        self._assess_verdicts = list(assess_verdicts or [])

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

    def assess(self, text: str, rubric: str) -> KnowledgeVerdict:
        if self._assess_verdicts:
            return self._assess_verdicts.pop(0)
        t = normalize(text).lower()
        words = t.split()
        if len(words) < 5 or t in _GREETINGS:
            return KnowledgeVerdict(is_knowledge=False, category="chitchat",
                                    confidence="HIGH", rationale="too short / greeting")
        return KnowledgeVerdict(is_knowledge=True, category="fact",
                                confidence="HIGH", rationale="substantive")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_knowledge_provider.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add llmwiki/providers/base.py llmwiki/providers/fake.py tests/unit/test_knowledge_provider.py
git commit -m "feat: add assess() knowledge-worthiness contract + fake impl"
```

---

### Task 3: LiteLLM `assess` implementation

**Files:**
- Modify: `llmwiki/providers/litellm_provider.py`
- Test: `tests/unit/test_litellm_provider.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/unit/test_litellm_provider.py
import json as _json
import types as _types
from llmwiki.providers.base import KnowledgeVerdict


def test_assess_parses_json(monkeypatch):
    payload = _json.dumps({"is_knowledge": True, "category": "fact",
                           "confidence": "HIGH", "rationale": "policy"})

    def fake_completion(model, messages, **kw):
        return {"choices": [{"message": {"content": payload}}]}

    monkeypatch.setattr(mod, "litellm", _types.SimpleNamespace(
        embedding=None, completion=fake_completion))
    v = mod.LiteLLMProvider().assess("text", "rubric")
    assert isinstance(v, KnowledgeVerdict) and v.is_knowledge is True


def test_assess_malformed_falls_back_to_review(monkeypatch):
    def fake_completion(model, messages, **kw):
        return {"choices": [{"message": {"content": "garbage"}}]}

    monkeypatch.setattr(mod, "litellm", _types.SimpleNamespace(
        embedding=None, completion=fake_completion))
    v = mod.LiteLLMProvider().assess("text", "rubric")
    # unparseable -> not-knowledge + LOW so the gate routes to REVIEW, never a confident drop
    assert v.is_knowledge is False and v.confidence == "LOW"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_litellm_provider.py -q`
Expected: FAIL — `AttributeError: 'LiteLLMProvider' object has no attribute 'assess'`

- [ ] **Step 3: Add `assess` to `llmwiki/providers/litellm_provider.py`**

Add this import and method. Change the import line at top:

```python
from llmwiki.providers.base import AdjudicatorVerdict, KnowledgeVerdict
```

Add a module-level prompt constant after `_PROMPT`:

```python
_ASSESS_PROMPT = (
    "You decide whether a piece of text is worth storing as durable knowledge, following the "
    "operator's RUBRIC. Respond with ONLY a JSON object: "
    '{"is_knowledge": true|false, "category": short string, '
    '"confidence": one of "HIGH"|"MEDIUM"|"LOW", "rationale": short string}.'
)
```

Add this method to the `LiteLLMProvider` class (after `adjudicate`):

```python
    def assess(self, text: str, rubric: str) -> KnowledgeVerdict:
        messages = [
            {"role": "system", "content": _ASSESS_PROMPT},
            {"role": "user", "content": f"RUBRIC:\n{rubric}\n\nTEXT:\n{text[:4000]}"},
        ]
        resp = litellm.completion(model=self.adjudicate_model, messages=messages, temperature=0)
        content = resp["choices"][0]["message"]["content"]
        try:
            return KnowledgeVerdict(**json.loads(content))
        except Exception:
            # fail safe: unparseable -> low-confidence non-knowledge -> gate routes to REVIEW
            return KnowledgeVerdict(is_knowledge=False, category="unknown",
                                    confidence="LOW", rationale="unparseable model output")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_litellm_provider.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/providers/litellm_provider.py tests/unit/test_litellm_provider.py
git commit -m "feat: litellm assess() with fail-safe parsing"
```

---

### Task 4: Gate interface (base)

**Files:**
- Create: `llmwiki/gates/__init__.py`, `llmwiki/gates/base.py`
- Test: `tests/unit/test_gates.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_gates.py
from llmwiki.gates.base import GateVerdict, GateContext
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider


def test_gate_verdict_defaults():
    v = GateVerdict(decision="PASS", gate="x")
    assert v.reason == "" and v.signals == {}


def test_gate_context_holds_config_and_provider():
    ctx = GateContext(config=CollectionConfig(), provider=FakeProvider())
    assert ctx.config.min_chars == 40
    assert hasattr(ctx.provider, "assess")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_gates.py -q`
Expected: FAIL — `ModuleNotFoundError: llmwiki.gates`

- [ ] **Step 3: Write `llmwiki/gates/base.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Protocol
from pydantic import BaseModel, Field
from llmwiki.config import CollectionConfig
from llmwiki.models import IncomingDocument
from llmwiki.providers.base import Provider

Decision = Literal["PASS", "REJECT", "REVIEW"]


class GateVerdict(BaseModel):
    decision: Decision
    gate: str
    reason: str = ""
    signals: dict = Field(default_factory=dict)


@dataclass
class GateContext:
    config: CollectionConfig
    provider: Provider


class Gate(Protocol):
    name: str
    def check(self, doc: IncomingDocument, ctx: GateContext) -> GateVerdict: ...
```

- [ ] **Step 4: Write `llmwiki/gates/__init__.py`**

```python
from llmwiki.gates.base import Gate, GateVerdict, GateContext

__all__ = ["Gate", "GateVerdict", "GateContext"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_gates.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add llmwiki/gates/__init__.py llmwiki/gates/base.py tests/unit/test_gates.py
git commit -m "feat: gate interface (GateVerdict, GateContext, Gate protocol)"
```

---

### Task 5: Built-in gates

**Files:**
- Create: `llmwiki/gates/builtin.py`
- Test: `tests/unit/test_gates.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/unit/test_gates.py
from llmwiki.gates.builtin import MinInfoGate, DenylistGate, KnowledgeWorthinessGate
from llmwiki.providers.base import KnowledgeVerdict
from llmwiki.models import IncomingDocument


def _doc(content):
    return IncomingDocument(collection="kb", content=content)


def _ctx(**overrides):
    return GateContext(config=CollectionConfig(**overrides), provider=FakeProvider())


def test_min_info_rejects_short_and_passes_long():
    assert MinInfoGate().check(_doc("ok thanks"), _ctx()).decision == "REJECT"
    long = "The enterprise refund window is thirty days from the invoice date."
    assert MinInfoGate().check(_doc(long), _ctx()).decision == "PASS"


def test_denylist_matches_with_configured_action():
    ctx = _ctx(denylist_patterns=[r"\bSSN\b"], denylist_action="REJECT")
    assert DenylistGate().check(_doc("user SSN is 123"), ctx).decision == "REJECT"
    assert DenylistGate().check(_doc("nothing sensitive here at all"), ctx).decision == "PASS"


def test_denylist_default_action_is_review():
    ctx = _ctx(denylist_patterns=[r"password"])
    assert DenylistGate().check(_doc("the password is hunter2 again"), ctx).decision == "REVIEW"


def _ctx_assess(verdict):
    return GateContext(config=CollectionConfig(),
                       provider=FakeProvider(assess_verdicts=[verdict]))


def test_knowledge_gate_routes_by_verdict():
    g = KnowledgeWorthinessGate()
    passv = g.check(_doc("x" * 50), _ctx_assess(KnowledgeVerdict(is_knowledge=True, confidence="HIGH")))
    assert passv.decision == "PASS"
    rej = g.check(_doc("x" * 50), _ctx_assess(KnowledgeVerdict(is_knowledge=False, confidence="HIGH")))
    assert rej.decision == "REJECT"
    rev = g.check(_doc("x" * 50), _ctx_assess(KnowledgeVerdict(is_knowledge=False, confidence="LOW")))
    assert rev.decision == "REVIEW"


def test_knowledge_gate_provider_error_is_review():
    class Boom(FakeProvider):
        def assess(self, text, rubric):
            raise RuntimeError("provider down")
    ctx = GateContext(config=CollectionConfig(), provider=Boom())
    assert KnowledgeWorthinessGate().check(_doc("x" * 50), ctx).decision == "REVIEW"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_gates.py -q`
Expected: FAIL — `ModuleNotFoundError: llmwiki.gates.builtin`

- [ ] **Step 3: Write `llmwiki/gates/builtin.py`**

```python
from __future__ import annotations
import re
from llmwiki.gates.base import GateVerdict, GateContext
from llmwiki.models import IncomingDocument
from llmwiki.text import normalize


class MinInfoGate:
    name = "min_info"

    def check(self, doc: IncomingDocument, ctx: GateContext) -> GateVerdict:
        n = len(normalize(doc.content))
        if n < ctx.config.min_chars:
            return GateVerdict(decision="REJECT", gate=self.name,
                               reason=f"below min_chars ({n} < {ctx.config.min_chars})",
                               signals={"length": n})
        return GateVerdict(decision="PASS", gate=self.name, signals={"length": n})


class DenylistGate:
    name = "denylist"

    def check(self, doc: IncomingDocument, ctx: GateContext) -> GateVerdict:
        for pattern in ctx.config.denylist_patterns:
            if re.search(pattern, doc.content):
                return GateVerdict(decision=ctx.config.denylist_action, gate=self.name,
                                   reason=f"matched denylist /{pattern}/",
                                   signals={"pattern": pattern})
        return GateVerdict(decision="PASS", gate=self.name)


class KnowledgeWorthinessGate:
    name = "knowledge"

    def check(self, doc: IncomingDocument, ctx: GateContext) -> GateVerdict:
        try:
            v = ctx.provider.assess(doc.content, ctx.config.knowledge_rubric)
        except Exception as e:
            return GateVerdict(decision="REVIEW", gate=self.name,
                               reason="assessor error", signals={"error": str(e)})
        signals = {"category": v.category, "confidence": v.confidence,
                   "is_knowledge": v.is_knowledge}
        if v.is_knowledge:
            return GateVerdict(decision="PASS", gate=self.name, signals=signals)
        if v.confidence == "HIGH":
            return GateVerdict(decision="REJECT", gate=self.name,
                               reason=f"not knowledge ({v.category})", signals=signals)
        return GateVerdict(decision="REVIEW", gate=self.name,
                           reason="borderline knowledge", signals=signals)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_gates.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/gates/builtin.py tests/unit/test_gates.py
git commit -m "feat: built-in gates (min_info, denylist, knowledge)"
```

---

### Task 6: Gate chain + registry

**Files:**
- Create: `llmwiki/gates/chain.py`
- Modify: `llmwiki/gates/__init__.py`
- Test: `tests/unit/test_gates.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/unit/test_gates.py
import pytest
from llmwiki.gates.chain import GateChain, build_chain, register_gate


def test_chain_returns_first_non_pass_and_short_circuits():
    calls = []

    class Tag:
        def __init__(self, name, decision):
            self.name = name
            self._d = decision
        def check(self, doc, ctx):
            calls.append(self.name)
            return GateVerdict(decision=self._d, gate=self.name)

    chain = GateChain([Tag("a", "PASS"), Tag("b", "REJECT"), Tag("c", "PASS")])
    v = chain.run(_doc("hi"), _ctx())
    assert v.decision == "REJECT" and v.gate == "b"
    assert calls == ["a", "b"]  # c never ran


def test_chain_all_pass_returns_none():
    class P:
        name = "p"
        def check(self, doc, ctx):
            return GateVerdict(decision="PASS", gate="p")
    assert GateChain([P()]).run(_doc("hi"), _ctx()) is None


def test_build_chain_from_config_order():
    cfg = CollectionConfig(gate_order=["denylist", "min_info"])
    chain = build_chain(cfg)
    assert [g.name for g in chain.gates] == ["denylist", "min_info"]


def test_build_chain_unknown_gate_raises():
    with pytest.raises(ValueError):
        build_chain(CollectionConfig(gate_order=["nope"]))


def test_register_custom_gate():
    class Custom:
        name = "custom"
        def check(self, doc, ctx):
            return GateVerdict(decision="PASS", gate="custom")
    register_gate("custom", Custom)
    chain = build_chain(CollectionConfig(gate_order=["custom"]))
    assert chain.gates[0].name == "custom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_gates.py -q`
Expected: FAIL — `ModuleNotFoundError: llmwiki.gates.chain`

- [ ] **Step 3: Write `llmwiki/gates/chain.py`**

```python
from __future__ import annotations
from llmwiki.gates.base import Gate, GateVerdict, GateContext
from llmwiki.gates.builtin import MinInfoGate, DenylistGate, KnowledgeWorthinessGate
from llmwiki.config import CollectionConfig
from llmwiki.models import IncomingDocument

_REGISTRY: dict[str, type] = {
    "min_info": MinInfoGate,
    "denylist": DenylistGate,
    "knowledge": KnowledgeWorthinessGate,
}


def register_gate(name: str, cls: type) -> None:
    _REGISTRY[name] = cls


def known_gates() -> set[str]:
    return set(_REGISTRY)


class GateChain:
    def __init__(self, gates: list[Gate]):
        self.gates = gates

    def run(self, doc: IncomingDocument, ctx: GateContext) -> GateVerdict | None:
        for gate in self.gates:
            verdict = gate.check(doc, ctx)
            if verdict.decision != "PASS":
                return verdict
        return None


def build_chain(config: CollectionConfig) -> GateChain:
    gates: list[Gate] = []
    for name in config.gate_order:
        cls = _REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"unknown gate '{name}'")
        gates.append(cls())
    return GateChain(gates)
```

- [ ] **Step 4: Update `llmwiki/gates/__init__.py`**

```python
from llmwiki.gates.base import Gate, GateVerdict, GateContext
from llmwiki.gates.chain import GateChain, build_chain, register_gate, known_gates

__all__ = ["Gate", "GateVerdict", "GateContext", "GateChain",
           "build_chain", "register_gate", "known_gates"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_gates.py -q`
Expected: PASS (12 passed)

- [ ] **Step 6: Commit**

```bash
git add llmwiki/gates/chain.py llmwiki/gates/__init__.py tests/unit/test_gates.py
git commit -m "feat: gate chain runner + registry + build_chain"
```

---

### Task 7: Per-collection config persistence + loading

**Files:**
- Modify: `llmwiki/storage/index_store.py` (add `set_collection_config`)
- Modify: `llmwiki/pipeline.py` (add `_load_config`)
- Test: `tests/integration/test_index_store.py` (append), `tests/integration/test_pipeline.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/integration/test_index_store.py
def test_set_collection_config_roundtrip(tmp_path):
    s = make_store(tmp_path); s.create_collection("kb")
    s.set_collection_config("kb", {"min_chars": 99, "quality_enabled": False})
    cfg = s.get_collection("kb")["config"]
    assert cfg["min_chars"] == 99 and cfg["quality_enabled"] is False
```

```python
# append to tests/integration/test_pipeline.py
from llmwiki.config import CollectionConfig


def test_load_config_returns_stored_then_default(tmp_path):
    svc = make_service(tmp_path)
    # no stored config -> default
    assert svc._load_config("kb").min_chars == 40
    svc.index.set_collection_config("kb", {"min_chars": 5})
    assert svc._load_config("kb").min_chars == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/integration/test_index_store.py::test_set_collection_config_roundtrip tests/integration/test_pipeline.py::test_load_config_returns_stored_then_default -q`
Expected: FAIL — `AttributeError: ... set_collection_config` / `_load_config`

- [ ] **Step 3: Add `set_collection_config` to `IndexStore`**

In `llmwiki/storage/index_store.py`, add this method to the `IndexStore` class (right after `get_collection`):

```python
    def set_collection_config(self, name: str, config: dict) -> None:
        import json as _json
        self._conn.execute(
            "INSERT INTO collections(name, config) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET config=excluded.config",
            (name, _json.dumps(config)))
        self._conn.commit()
```

- [ ] **Step 4: Add `_load_config` to `IngestService`**

In `llmwiki/pipeline.py`, add this method to `IngestService` (after `__init__`):

```python
    def _load_config(self, collection: str) -> CollectionConfig:
        row = self.index.get_collection(collection)
        stored = row.get("config") if row else None
        return CollectionConfig(**stored) if stored else self.config
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_index_store.py tests/integration/test_pipeline.py -q`
Expected: PASS (all green)

- [ ] **Step 6: Commit**

```bash
git add llmwiki/storage/index_store.py llmwiki/pipeline.py tests/integration/test_index_store.py tests/integration/test_pipeline.py
git commit -m "feat: per-collection config persistence + loading"
```

---

### Task 8: Run the gate chain in the pipeline

**Files:**
- Modify: `llmwiki/pipeline.py`
- Test: `tests/integration/test_pipeline_quality.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_pipeline_quality.py
from llmwiki.pipeline import IngestService
from llmwiki.storage import IndexStore
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import KnowledgeVerdict
from llmwiki.models import IncomingDocument, Outcome


class SpyProvider(FakeProvider):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.embed_calls = 0

    def embed(self, text):
        self.embed_calls += 1
        return super().embed(text)


def make_service(tmp_path, provider):
    svc = IngestService(IndexStore(str(tmp_path / "idx.db")), str(tmp_path / "r"), provider)
    svc.ensure_collection("kb")
    return svc


def ingest(svc, content, **kw):
    return svc.ingest(IncomingDocument(collection="kb", content=content, **kw))


def test_short_junk_rejected_without_embedding(tmp_path):
    prov = SpyProvider()
    svc = make_service(tmp_path, prov)
    rec = ingest(svc, "ok thanks")
    assert rec.outcome is Outcome.REJECTED
    assert rec.signals.get("gate") == "min_info"
    assert prov.embed_calls == 0          # rejected before embedding


def test_knowledge_gate_reject_high_confidence(tmp_path):
    prov = SpyProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=False, confidence="HIGH")])
    svc = make_service(tmp_path, prov)
    rec = ingest(svc, "this is a long enough sentence to clear the min info gate easily")
    assert rec.outcome is Outcome.REJECTED and rec.signals.get("gate") == "knowledge"


def test_borderline_routes_to_review_with_item(tmp_path):
    prov = SpyProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=False, confidence="MEDIUM")])
    svc = make_service(tmp_path, prov)
    rec = ingest(svc, "this is a long enough sentence to clear the min info gate easily")
    assert rec.outcome is Outcome.NEEDS_REVIEW
    assert any(r.decision_id == rec.id for r in svc.index.list_reviews("kb"))


def test_real_knowledge_passes_to_new(tmp_path):
    prov = SpyProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=True, confidence="HIGH")])
    svc = make_service(tmp_path, prov)
    rec = ingest(svc, "The enterprise refund window is thirty days from the invoice date.")
    assert rec.outcome is Outcome.NEW and prov.embed_calls == 1


def test_quality_disabled_bypasses_gates(tmp_path):
    prov = SpyProvider()
    svc = make_service(tmp_path, prov)
    svc.index.set_collection_config("kb", {"quality_enabled": False})
    rec = ingest(svc, "ok thanks")        # would normally be rejected by min_info
    assert rec.outcome is Outcome.NEW
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_pipeline_quality.py -q`
Expected: FAIL — `REJECTED` not produced (gates not wired); `test_short_junk_rejected` fails.

- [ ] **Step 3: Wire the gate chain into `IngestService.ingest`**

In `llmwiki/pipeline.py`, update the imports at the top to add the gate imports and `GateContext`:

```python
from llmwiki.gates.chain import build_chain
from llmwiki.gates.base import GateContext
```

Then replace the body of `ingest` (the method, lines from `# idempotency short-circuit` through the existing `chash = content_hash(...)` block) so the gate chain runs after idempotency and before embedding. The full updated method:

```python
    def ingest(self, doc: IncomingDocument, principal_id: str | None = None) -> DecisionRecord:
        # idempotency short-circuit
        if doc.idempotency_key:
            prior = self.index.get_decision_by_idempotency(doc.collection, doc.idempotency_key)
            if prior is not None:
                return prior

        cfg = self._load_config(doc.collection)

        # quality gates run BEFORE embedding so rejected content costs nothing downstream
        if cfg.quality_enabled:
            verdict = build_chain(cfg).run(doc, GateContext(config=cfg, provider=self.provider))
            if verdict is not None and verdict.decision != "PASS":
                return self._record_gate_block(doc, verdict, principal_id)

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
        decision = classify(fp, candidates, cfg, self.provider.adjudicate)

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

    def _record_gate_block(self, doc: IncomingDocument, verdict, principal_id) -> DecisionRecord:
        outcome = Outcome.REJECTED if verdict.decision == "REJECT" else Outcome.NEEDS_REVIEW
        rec = DecisionRecord(id=_id("dec"), collection=doc.collection, outcome=outcome,
                             content_hash=content_hash(doc.content), principal_id=principal_id,
                             reason=verdict.reason,
                             signals={"gate": verdict.gate, **verdict.signals})
        self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
        if outcome is Outcome.NEEDS_REVIEW:
            self.index.save_review(ReviewItem(
                id=_id("rev"), decision_id=rec.id, collection=doc.collection,
                candidates=[{"gate": verdict.gate, "reason": verdict.reason, **verdict.signals}]))
        return rec
```

(Note: `classify(...)` now uses the loaded `cfg` instead of `self.config` — per-collection thresholds now apply too.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_pipeline_quality.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the existing pipeline tests to confirm no regression**

Run: `.venv/bin/pytest tests/integration/test_pipeline.py tests/integration/test_coordinator.py -q`
Expected: PASS (all green — default config has `quality_enabled=True`, but those tests use substantive multi-word text that clears min_info and the fake heuristic marks as knowledge)

- [ ] **Step 6: Commit**

```bash
git add llmwiki/pipeline.py tests/integration/test_pipeline_quality.py
git commit -m "feat: run quality gate chain in ingest before embedding"
```

---

### Task 9: Collection-config REST endpoints

**Files:**
- Modify: `llmwiki/api/schemas.py`, `llmwiki/api/routers.py`
- Test: `tests/integration/test_collection_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_collection_config.py
from fastapi.testclient import TestClient
from llmwiki.api.app import create_app
from llmwiki.auth.base import Principal
from llmwiki.auth.apikey import ApiKeyAuthenticator


def make_client(tmp_path):
    auth = ApiKeyAuthenticator({
        "admin": Principal(id="admin", allowed_collections=["kb"], roles=["admin"]),
        "writer": Principal(id="w", allowed_collections=["kb"], roles=["ingest", "read"]),
    })
    app = create_app(data_dir=str(tmp_path), authenticator=auth, provider_name="fake")
    c = TestClient(app)
    c.post("/v1/collections", json={"name": "kb"}, headers={"authorization": "Bearer admin"})
    return c


A = {"authorization": "Bearer admin"}
W = {"authorization": "Bearer writer"}


def test_put_and_get_config(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/v1/collections/kb/config", json={"min_chars": 5, "quality_enabled": True}, headers=A)
    assert r.status_code == 200
    g = c.get("/v1/collections/kb/config", headers=A)
    assert g.status_code == 200 and g.json()["min_chars"] == 5


def test_put_config_requires_admin(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/v1/collections/kb/config", json={"min_chars": 5}, headers=W)
    assert r.status_code == 403


def test_invalid_regex_is_422(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/v1/collections/kb/config", json={"denylist_patterns": ["("]}, headers=A)
    assert r.status_code == 422


def test_per_collection_rules_change_outcome(tmp_path):
    c = make_client(tmp_path)
    # tighten min_chars so a medium sentence is rejected
    c.put("/v1/collections/kb/config", json={"min_chars": 1000}, headers=A)
    r = c.post("/v1/collections/kb/documents",
               json={"content": "The refund window is thirty days."}, headers=W)
    assert r.json()["outcome"] == "REJECTED"
    # loosen it -> same content is accepted
    c.put("/v1/collections/kb/config", json={"min_chars": 1}, headers=A)
    r2 = c.post("/v1/collections/kb/documents",
                json={"content": "The refund window is thirty days.", "declared_id": "p1"}, headers=W)
    assert r2.json()["outcome"] in ("NEW", "UPDATE")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_collection_config.py -q`
Expected: FAIL — `404` on `/config` (endpoints don't exist)

- [ ] **Step 3: Update `llmwiki/api/schemas.py`**

Add `config` to `CreateCollectionRequest`:

```python
class CreateCollectionRequest(BaseModel):
    name: str
    config: dict[str, Any] | None = None
```

- [ ] **Step 4: Add the config endpoints in `llmwiki/api/routers.py`**

Add these imports at the top:

```python
import re
from llmwiki.config import CollectionConfig
```

Add a validation helper above `build_router`:

```python
def _validate_and_dump_config(config: dict) -> dict:
    try:
        cfg = CollectionConfig(**config)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid config: {e}")
    for pattern in cfg.denylist_patterns:
        try:
            re.compile(pattern)
        except re.error as e:
            raise HTTPException(status_code=422, detail=f"invalid denylist regex /{pattern}/: {e}")
    return cfg.model_dump()
```

Inside `build_router`, update `create_collection` to persist optional config, and add the two config routes:

```python
    @r.post("/collections")
    def create_collection(body: CreateCollectionRequest, request: Request):
        principal = _principal(request)
        request.app.state.service.ensure_collection(body.name)
        if body.config is not None:
            _check(principal, body.name, "admin")
            request.app.state.index.set_collection_config(
                body.name, _validate_and_dump_config(body.config))
        return {"name": body.name}

    @r.get("/collections/{collection}/config")
    def get_config(collection: str, request: Request):
        principal = _principal(request)
        _check(principal, collection, "read")
        row = request.app.state.index.get_collection(collection)
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        return CollectionConfig(**row["config"]).model_dump() if row["config"] \
            else CollectionConfig().model_dump()

    @r.put("/collections/{collection}/config")
    def put_config(collection: str, body: dict, request: Request):
        principal = _principal(request)
        _check(principal, collection, "admin")
        if request.app.state.index.get_collection(collection) is None:
            raise HTTPException(status_code=404, detail="not found")
        dumped = _validate_and_dump_config(body)
        request.app.state.index.set_collection_config(collection, dumped)
        return dumped
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_collection_config.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add llmwiki/api/schemas.py llmwiki/api/routers.py tests/integration/test_collection_config.py
git commit -m "feat: per-collection config REST endpoints (GET/PUT, admin-gated)"
```

---

### Task 10: Full suite + README

**Files:**
- Modify: `README.md`
- Test: full suite

- [ ] **Step 1: Run the entire suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all green — ~80 tests)

- [ ] **Step 2: Add a "Quality gates" section to `README.md`**

Insert after the "How the decision works" section:

```markdown
## Quality-control gates

Before dedup, each document passes a configurable chain of gates (default order
`min_info → denylist → knowledge`). Gates run **before embedding**, so filtered content costs
nothing. A gate returns PASS / REJECT / REVIEW; REJECT → `REJECTED` (logged), REVIEW →
`NEEDS_REVIEW` (human queue), PASS → dedup/version.

The `knowledge` gate asks the LLM whether the text is worth storing, using a per-collection
**rubric** you control. Set rules per collection (admin only):

    curl -X PUT localhost:8000/v1/collections/kb/config -H "authorization: Bearer $KEY" \
      -H 'content-type: application/json' \
      -d '{"min_chars":40,"knowledge_rubric":"Keep durable facts and customer preferences; drop greetings and small talk.","denylist_patterns":["\\bSSN\\b"],"denylist_action":"REVIEW"}'

Disable filtering for a collection with `{"quality_enabled": false}`.
```

- [ ] **Step 3: Final full-suite run + commit**

Run: `.venv/bin/pytest -q`
Expected: PASS

```bash
git add README.md
git commit -m "docs: document quality-control gates + per-collection config"
```

---

## Self-Review Notes (plan vs. spec)

- **Pluggable chain, runs after authz before embed** → Task 8 (chain before `embed`); Tasks 4–6 (chain). ✅
- **Deterministic gates + LLM rubric gate** → Task 5 (`MinInfoGate`, `DenylistGate`, `KnowledgeWorthinessGate`). ✅
- **`assess(text, rubric)` contract + confidence routing** → Task 2 (base/fake), Task 3 (litellm), Task 5 (routing). ✅
- **Confidence-based routing; never auto-drop possible knowledge** → Task 5 (`is_knowledge=False`+HIGH→REJECT else REVIEW; error→REVIEW). ✅
- **Reuse `REJECTED` + `signals.gate`/`reason`** → Task 8 (`_record_gate_block`). ✅
- **User-defined rules per collection** → Task 1 (config fields), Task 7 (persist+load), Task 9 (GET/PUT). ✅
- **Fix existing gap: per-collection config wired in** → Task 7 (`_load_config`, used in Task 8 for both gates AND classify). ✅
- **Regex validated at set time → 422; LLM fail-safe → REVIEW; gates before embed; idempotency honored** → Task 9 (validation), Task 3/Task 5 (fail-safe), Task 8 (`embed_calls==0` test, idempotency_key passed through). ✅
- **Testing: per-gate, chain, assessor, pipeline integration, per-collection config, config API, contract** → Tasks 2,3,5,6,8,9. ✅
- **Type consistency:** `GateVerdict.decision ∈ {PASS,REJECT,REVIEW}`; `denylist_action ∈ {REJECT,REVIEW}` (a subset, valid as a verdict decision); `KnowledgeVerdict` fields consistent across base/fake/litellm/gate; `build_chain` consumes `gate_order`; gate `name`s match registry keys. ✅
```
