# Composable Gate & Rule Framework (SP-G) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the hard-coded gate chain + classifier into a data-driven, admin-composable rule engine: gates contain categorized rules drawn from a parameterized palette, evaluated first-match/short-circuit with a shared context.

**Architecture:** A new `llmwiki/rules/` package defines rule primitives (deterministic + semantic), an `EvalContext` that lazily exposes fingerprint/candidates/findings, and an engine that builds a pipeline from per-collection config and evaluates it. `IngestService` calls the engine and maps the resulting disposition onto the existing store/review machinery. A default pipeline reproduces current behavior (parity-tested). Adds `REPLACE`.

**Tech Stack:** Python 3.11+, pydantic v2, pytest. Reuses the existing `Provider`, `IndexStore`/`ContentStore`, `DecisionRecord`, review queue.

**Spec:** `docs/superpowers/specs/2026-06-06-composable-gate-rule-framework-design.md`

---

## File Structure

```
llmwiki/
  models.py            # MODIFY: Outcome.REPLACE
  providers/base.py    # MODIFY: SUPERSEDES in Relationship vocabulary
  rules/
    __init__.py        # CREATE: exports
    base.py            # CREATE: Disposition, RuleResult, Candidate, _cosine, EvalContext, Rule, registry
    palette.py         # CREATE: built-in primitives (validity/existence/update-replace/routing)
    engine.py          # CREATE: build_pipeline(config) + evaluate(ctx)
  config.py            # MODIFY: pipeline field + default_pipeline()
  pipeline.py          # MODIFY: call engine; map disposition -> apply; REPLACE apply
  storage/index_store.py  # MODIFY: import Candidate from rules.base; mark_replaced()
  api/routers.py       # MODIFY: validate pipeline config
  classifier.py        # REMOVE (logic re-homed into rules/palette.py)
  gates/               # REMOVE (logic re-homed into rules/palette.py)
tests/
  unit/test_rules_base.py
  unit/test_rules_validity.py
  unit/test_rules_existence.py
  unit/test_rules_update_replace.py
  unit/test_rules_routing.py
  unit/test_rule_engine.py
  integration/test_pipeline_parity.py
  ... (existing tests updated where they referenced the old config shape)
```

---

### Task 1: `Outcome.REPLACE` + adjudicator `SUPERSEDES`

**Files:**
- Modify: `llmwiki/models.py`, `llmwiki/providers/base.py`
- Test: `tests/unit/test_models.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/unit/test_models.py
def test_outcome_has_replace():
    assert Outcome.REPLACE.value == "REPLACE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_models.py::test_outcome_has_replace -q`
Expected: FAIL — `AttributeError: REPLACE`

- [ ] **Step 3: Add `REPLACE` to `Outcome` in `llmwiki/models.py`**

```python
class Outcome(str, Enum):
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"
    UPDATE = "UPDATE"
    NEW = "NEW"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REPLACE = "REPLACE"
```

- [ ] **Step 4: Add `SUPERSEDES` to the Relationship vocabulary in `llmwiki/providers/base.py`**

```python
Relationship = Literal["SAME_UPDATED", "DIFFERENT", "RELATED_BUT_DISTINCT", "CONFLICTING", "SUPERSEDES"]
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/unit/test_models.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add llmwiki/models.py llmwiki/providers/base.py tests/unit/test_models.py
git commit -m "feat: add Outcome.REPLACE and SUPERSEDES relationship"
```

---

### Task 2: `rules/base.py` — primitives infrastructure

**Files:**
- Create: `llmwiki/rules/__init__.py`, `llmwiki/rules/base.py`
- Modify: `llmwiki/storage/index_store.py` (import `Candidate` from `rules.base`)
- Test: `tests/unit/test_rules_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rules_base.py
from llmwiki.rules.base import (Candidate, EvalContext, RuleResult, cosine,
                                register_rule, get_rule, known_rules)
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.models import IncomingDocument


def test_cosine_basic():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_context_memoizes_hash_and_candidates():
    calls = {"n": 0}
    def loader():
        calls["n"] += 1
        return [Candidate(document_id="d1", content_hash="h", embedding=[1.0, 0.0])]
    ctx = EvalContext(doc=IncomingDocument(collection="kb", content="hello world facts here"),
                      provider=FakeProvider(), config=CollectionConfig(), candidates_loader=loader)
    assert len(ctx.content_hash) == 64
    ctx.candidates(); ctx.candidates()
    assert calls["n"] == 1  # memoized
    assert isinstance(ctx.embedding, list)


def test_registry_round_trip():
    class Dummy:
        id = "dummy"; category = "validity"; kind = "deterministic"
        class Params(__import__("pydantic").BaseModel):
            pass
        def evaluate(self, ctx, params):
            return RuleResult(disposition="PASS")
    register_rule(Dummy())
    assert "dummy" in known_rules()
    assert get_rule("dummy").category == "validity"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_rules_base.py -q`
Expected: FAIL — `ModuleNotFoundError: llmwiki.rules`

- [ ] **Step 3: Write `llmwiki/rules/base.py`**

```python
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Callable, Literal, Protocol
from pydantic import BaseModel
from llmwiki.config import CollectionConfig
from llmwiki.models import IncomingDocument
from llmwiki.providers.base import Provider
from llmwiki.text import content_hash, shingles

Disposition = Literal["PASS", "REJECT", "REVIEW", "DUPLICATE", "UPDATE", "REPLACE", "ACCEPT"]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


@dataclass
class Candidate:
    document_id: str
    content_hash: str
    embedding: list[float]
    shingles: set[str] = field(default_factory=set)
    content: str = ""
    identity_match: bool = False


@dataclass
class RuleResult:
    disposition: Disposition
    signals: dict = field(default_factory=dict)
    context_updates: dict = field(default_factory=dict)


@dataclass
class EvalContext:
    doc: IncomingDocument
    provider: Provider
    config: CollectionConfig
    candidates_loader: Callable[[], list[Candidate]] = lambda: []
    findings: dict = field(default_factory=dict)
    _hash: str | None = None
    _embedding: list[float] | None = None
    _shingles: set[str] | None = None
    _candidates: list[Candidate] | None = None

    @property
    def content_hash(self) -> str:
        if self._hash is None:
            self._hash = content_hash(self.doc.content)
        return self._hash

    @property
    def embedding(self) -> list[float]:
        if self._embedding is None:
            self._embedding = self.provider.embed(self.doc.content)
        return self._embedding

    @property
    def shingles(self) -> set[str]:
        if self._shingles is None:
            self._shingles = shingles(self.doc.content)
        return self._shingles

    @property
    def identity(self) -> str | None:
        return self.doc.declared_id or self.doc.source_uri

    def candidates(self) -> list[Candidate]:
        if self._candidates is None:
            self._candidates = self.candidates_loader()
        return self._candidates


class Rule(Protocol):
    id: str
    category: str
    kind: str  # "deterministic" | "semantic"
    Params: type[BaseModel]
    def evaluate(self, ctx: EvalContext, params: BaseModel) -> RuleResult: ...


_REGISTRY: dict[str, Rule] = {}


def register_rule(rule: Rule) -> None:
    _REGISTRY[rule.id] = rule


def get_rule(rule_id: str) -> Rule:
    if rule_id not in _REGISTRY:
        raise ValueError(f"unknown rule type '{rule_id}'")
    return _REGISTRY[rule_id]


def known_rules() -> set[str]:
    return set(_REGISTRY)
```

- [ ] **Step 4: Write `llmwiki/rules/__init__.py`**

```python
from llmwiki.rules.base import (Candidate, EvalContext, RuleResult, Disposition,
                                cosine, register_rule, get_rule, known_rules)

__all__ = ["Candidate", "EvalContext", "RuleResult", "Disposition", "cosine",
           "register_rule", "get_rule", "known_rules"]
```

- [ ] **Step 5: Point `index_store.py` at the re-homed `Candidate`**

In `llmwiki/storage/index_store.py`, change the import line:

```python
from llmwiki.rules.base import Candidate
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/pytest tests/unit/test_rules_base.py tests/integration/test_index_store.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add llmwiki/rules/__init__.py llmwiki/rules/base.py llmwiki/storage/index_store.py tests/unit/test_rules_base.py
git commit -m "feat: rules infrastructure (EvalContext, RuleResult, registry)"
```

---

### Task 3: Validity primitives

**Files:**
- Create: `llmwiki/rules/palette.py`
- Test: `tests/unit/test_rules_validity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rules_validity.py
from llmwiki.rules.palette import MinLength, RegexDenylist, KnowledgeWorthiness, ContentType
from llmwiki.rules.base import EvalContext
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import KnowledgeVerdict
from llmwiki.models import IncomingDocument


def ctx(content, provider=None, content_type="text/plain"):
    return EvalContext(doc=IncomingDocument(collection="kb", content=content, content_type=content_type),
                       provider=provider or FakeProvider(), config=CollectionConfig())


def run(rule, c, **params):
    return rule.evaluate(c, rule.Params(**params))


def test_min_length():
    assert run(MinLength(), ctx("ok"), min_chars=40).disposition == "REJECT"
    assert run(MinLength(), ctx("x" * 50), min_chars=40).disposition == "PASS"


def test_content_type():
    assert run(ContentType(), ctx("hi", content_type="application/zip"),
               allowed=["text/plain"]).disposition == "REJECT"
    assert run(ContentType(), ctx("hi", content_type="text/plain"),
               allowed=["text/plain"]).disposition == "PASS"


def test_regex_denylist():
    r = run(RegexDenylist(), ctx("my SSN is x"), patterns=[r"\bSSN\b"], action="REVIEW")
    assert r.disposition == "REVIEW"
    assert run(RegexDenylist(), ctx("nothing here"), patterns=[r"\bSSN\b"], action="REVIEW").disposition == "PASS"


def test_knowledge_worthiness_routes_by_verdict():
    kw = KnowledgeWorthiness()
    p = FakeProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=False, confidence="HIGH")])
    assert kw.evaluate(ctx("x" * 50, p), kw.Params(rubric="r", on_uncertain="REVIEW")).disposition == "REJECT"
    p2 = FakeProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=False, confidence="LOW")])
    assert kw.evaluate(ctx("x" * 50, p2), kw.Params(rubric="r", on_uncertain="REVIEW")).disposition == "REVIEW"
    p3 = FakeProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=True, confidence="HIGH")])
    assert kw.evaluate(ctx("x" * 50, p3), kw.Params(rubric="r", on_uncertain="REVIEW")).disposition == "PASS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_rules_validity.py -q`
Expected: FAIL — `ModuleNotFoundError`/`ImportError`

- [ ] **Step 3: Write `llmwiki/rules/palette.py` (validity section)**

```python
from __future__ import annotations
import re
from typing import Literal
from pydantic import BaseModel, Field
from llmwiki.rules.base import EvalContext, RuleResult, cosine
from llmwiki.text import jaccard, normalize

_LEXICAL_FLOOR = 0.10


# ---------- Validity ----------

class MinLength:
    id = "min_length"; category = "validity"; kind = "deterministic"

    class Params(BaseModel):
        min_chars: int = 40

    def evaluate(self, ctx: EvalContext, params: "MinLength.Params") -> RuleResult:
        n = len(normalize(ctx.doc.content))
        if n < params.min_chars:
            return RuleResult("REJECT", {"length": n, "rule": self.id})
        return RuleResult("PASS", {"length": n})


class ContentType:
    id = "content_type"; category = "validity"; kind = "deterministic"

    class Params(BaseModel):
        allowed: list[str] = Field(default_factory=lambda: ["text/plain", "text/markdown", "text/html"])

    def evaluate(self, ctx: EvalContext, params: "ContentType.Params") -> RuleResult:
        if ctx.doc.content_type not in params.allowed:
            return RuleResult("REJECT", {"content_type": ctx.doc.content_type, "rule": self.id})
        return RuleResult("PASS")


class RegexDenylist:
    id = "regex_denylist"; category = "validity"; kind = "deterministic"

    class Params(BaseModel):
        patterns: list[str] = Field(default_factory=list)
        action: Literal["REJECT", "REVIEW"] = "REVIEW"

    def evaluate(self, ctx: EvalContext, params: "RegexDenylist.Params") -> RuleResult:
        for pat in params.patterns:
            if re.search(pat, ctx.doc.content):
                return RuleResult(params.action, {"pattern": pat, "rule": self.id})
        return RuleResult("PASS")


class KnowledgeWorthiness:
    id = "knowledge_worthiness"; category = "validity"; kind = "semantic"

    class Params(BaseModel):
        rubric: str = ""
        on_uncertain: Literal["REVIEW", "REJECT"] = "REVIEW"

    def evaluate(self, ctx: EvalContext, params: "KnowledgeWorthiness.Params") -> RuleResult:
        try:
            v = ctx.provider.assess(ctx.doc.content, params.rubric or ctx.config.knowledge_rubric)
        except Exception as e:
            return RuleResult("REVIEW", {"rule": self.id, "error": str(e)})
        sig = {"rule": self.id, "category": v.category, "confidence": v.confidence,
               "is_knowledge": v.is_knowledge}
        if v.is_knowledge:
            return RuleResult("PASS", sig)
        if v.confidence == "HIGH":
            return RuleResult("REJECT", sig)
        return RuleResult(params.on_uncertain, sig)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/unit/test_rules_validity.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/rules/palette.py tests/unit/test_rules_validity.py
git commit -m "feat: validity rule primitives"
```

---

### Task 4: Existence primitives

**Files:**
- Modify: `llmwiki/rules/palette.py` (append Existence section)
- Test: `tests/unit/test_rules_existence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rules_existence.py
from llmwiki.rules.palette import ExactDuplicate, IdentityMatch, SemanticDuplicate
from llmwiki.rules.base import EvalContext, Candidate
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import AdjudicatorVerdict
from llmwiki.models import IncomingDocument


def make_ctx(content, candidates, provider=None, declared_id=None):
    return EvalContext(
        doc=IncomingDocument(collection="kb", content=content, declared_id=declared_id),
        provider=provider or FakeProvider(), config=CollectionConfig(),
        candidates_loader=lambda: candidates)


def test_exact_duplicate_fires_on_hash_match():
    c = make_ctx("the cat sat on the mat here", [])
    cand = Candidate(document_id="d1", content_hash=c.content_hash, embedding=[1.0, 0.0])
    c2 = make_ctx("the cat sat on the mat here", [cand])
    r = ExactDuplicate().evaluate(c2, ExactDuplicate.Params())
    assert r.disposition == "DUPLICATE"


def test_identity_match_same_hash_duplicate_diff_annotates_pass():
    base = make_ctx("first body version here now", [], declared_id="doc-1")
    same_hash = base.content_hash
    cand = Candidate(document_id="d1", content_hash=same_hash, embedding=[1.0, 0.0], identity_match=True)
    c = make_ctx("first body version here now", [cand], declared_id="doc-1")
    assert IdentityMatch().evaluate(c, IdentityMatch.Params()).disposition == "DUPLICATE"

    cand2 = Candidate(document_id="d1", content_hash="other", embedding=[1.0, 0.0], identity_match=True)
    c2 = make_ctx("changed body version here now", [cand2], declared_id="doc-1")
    r = IdentityMatch().evaluate(c2, IdentityMatch.Params())
    assert r.disposition == "PASS" and r.context_updates["match"].document_id == "d1"


def test_semantic_duplicate_low_sim_pass_high_sim_annotates_match():
    far = Candidate(document_id="d1", content_hash="h", embedding=[0.0, 1.0], content="x")
    c = make_ctx("alpha beta gamma delta epsilon", [far])
    # orthogonal -> below gray band -> PASS, no match
    r = SemanticDuplicate().evaluate(c, SemanticDuplicate.Params())
    assert r.disposition == "PASS" and "match" not in r.context_updates

    # identical embedding (same tokens) but different hash -> high sim -> annotate match + PASS
    near = Candidate(document_id="d2", content_hash="other",
                     embedding=FakeProvider().embed("alpha beta gamma delta epsilon"),
                     shingles=set(), content="alpha beta gamma delta epsilon")
    c2 = make_ctx("alpha beta gamma delta epsilon zeta", [near])
    r2 = SemanticDuplicate().evaluate(c2, SemanticDuplicate.Params(threshold_high=0.5, gray_band=0.2))
    assert r2.disposition in ("PASS", "REVIEW")  # depends on lexical; match annotated when PASS at high sim
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_rules_existence.py -q`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Append the Existence section to `llmwiki/rules/palette.py`**

```python
# ---------- Existence ----------

def _ranked(ctx):
    cands = ctx.candidates()
    scored = sorted(((c, cosine(ctx.embedding, c.embedding)) for c in cands),
                    key=lambda t: t[1], reverse=True)
    return scored


class ExactDuplicate:
    id = "exact_duplicate"; category = "existence"; kind = "deterministic"

    class Params(BaseModel):
        pass

    def evaluate(self, ctx: EvalContext, params: "ExactDuplicate.Params") -> RuleResult:
        for c in ctx.candidates():
            if c.content_hash == ctx.content_hash:
                return RuleResult("DUPLICATE", {"rule": self.id, "document_id": c.document_id},
                                  {"match": c})
        return RuleResult("PASS")


class IdentityMatch:
    id = "identity_match"; category = "existence"; kind = "deterministic"

    class Params(BaseModel):
        pass

    def evaluate(self, ctx: EvalContext, params: "IdentityMatch.Params") -> RuleResult:
        match = next((c for c in ctx.candidates() if c.identity_match), None)
        if match is None:
            return RuleResult("PASS")
        if match.content_hash == ctx.content_hash:
            return RuleResult("DUPLICATE", {"rule": self.id, "document_id": match.document_id},
                              {"match": match})
        return RuleResult("PASS", {"rule": self.id, "document_id": match.document_id},
                          {"match": match})


class SemanticDuplicate:
    id = "semantic_duplicate"; category = "existence"; kind = "semantic"

    class Params(BaseModel):
        threshold_high: float = 0.97
        gray_band: float = 0.80
        margin: float = 0.02
        on_uncertain: Literal["REVIEW"] = "REVIEW"

    def evaluate(self, ctx: EvalContext, params: "SemanticDuplicate.Params") -> RuleResult:
        scored = _ranked(ctx)
        if not scored:
            return RuleResult("PASS")
        top, top_sim = scored[0]
        second = scored[1][1] if len(scored) > 1 else 0.0
        sig = {"rule": self.id, "top_sim": round(top_sim, 4), "margin": round(top_sim - second, 4)}
        # always record nearest neighbour for downstream replace logic
        updates = {"top_candidate": top, "top_sim": top_sim}
        if top.content_hash == ctx.content_hash:
            return RuleResult("DUPLICATE", {**sig, "document_id": top.document_id}, {"match": top, **updates})
        if top_sim < params.gray_band:
            return RuleResult("PASS", sig, updates)
        if len(scored) > 1 and (top_sim - second) < params.margin:
            return RuleResult("REVIEW", {**sig, "why": "margin"}, updates)
        lex = jaccard(ctx.shingles, top.shingles)
        sig["lexical"] = round(lex, 4)
        if top_sim >= params.threshold_high:
            if lex < _LEXICAL_FLOOR:
                return RuleResult("REVIEW", {**sig, "why": "conflict"}, updates)
            return RuleResult("PASS", sig, {"match": top, **updates})
        # gray band -> adjudicate
        verdict = ctx.provider.adjudicate(ctx.content_hash, top.content)
        sig.update(relationship=verdict.relationship, confidence=verdict.confidence)
        if verdict.confidence != "HIGH":
            return RuleResult("REVIEW", {**sig, "why": "lowconf"}, updates)
        if verdict.relationship in ("RELATED_BUT_DISTINCT", "CONFLICTING"):
            return RuleResult("REVIEW", {**sig, "why": verdict.relationship}, updates)
        if lex < _LEXICAL_FLOOR:
            return RuleResult("REVIEW", {**sig, "why": "conflict"}, updates)
        if verdict.relationship == "SAME_UPDATED":
            return RuleResult("PASS", sig, {"match": top, **updates})
        return RuleResult("PASS", sig, updates)  # DIFFERENT -> new
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/unit/test_rules_existence.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/rules/palette.py tests/unit/test_rules_existence.py
git commit -m "feat: existence rule primitives (exact/identity/semantic duplicate)"
```

---

### Task 5: Update / Replace primitives

**Files:**
- Modify: `llmwiki/rules/palette.py` (append)
- Test: `tests/unit/test_rules_update_replace.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rules_update_replace.py
from llmwiki.rules.palette import VersionOnChange, SemanticReplace
from llmwiki.rules.base import EvalContext, Candidate
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import AdjudicatorVerdict
from llmwiki.models import IncomingDocument


def ctx(content, findings, provider=None, metadata=None):
    c = EvalContext(doc=IncomingDocument(collection="kb", content=content, metadata=metadata or {}),
                    provider=provider or FakeProvider(), config=CollectionConfig(),
                    candidates_loader=lambda: [])
    c.findings.update(findings)
    return c


def test_version_on_change_updates_when_match_present():
    m = Candidate(document_id="d1", content_hash="old", embedding=[1.0, 0.0])
    r = VersionOnChange().evaluate(ctx("new body", {"match": m}), VersionOnChange.Params())
    assert r.disposition == "UPDATE" and r.signals["document_id"] == "d1"


def test_version_on_change_passes_without_match():
    assert VersionOnChange().evaluate(ctx("body", {}), VersionOnChange.Params()).disposition == "PASS"


def test_semantic_replace_supersedes_with_signal():
    top = Candidate(document_id="old1", content_hash="h", embedding=[1.0, 0.0], content="old")
    prov = FakeProvider(verdicts=[AdjudicatorVerdict(relationship="SUPERSEDES", confidence="HIGH")])
    c = ctx("new", {"top_candidate": top, "top_sim": 0.95}, provider=prov,
            metadata={"supersedes": "old1"})
    r = SemanticReplace().evaluate(c, SemanticReplace.Params(threshold=0.9))
    assert r.disposition == "REPLACE" and r.signals["document_id"] == "old1"


def test_semantic_replace_no_signal_reviews_by_default():
    top = Candidate(document_id="old1", content_hash="h", embedding=[1.0, 0.0], content="old")
    prov = FakeProvider(verdicts=[AdjudicatorVerdict(relationship="SUPERSEDES", confidence="HIGH")])
    c = ctx("new", {"top_candidate": top, "top_sim": 0.95}, provider=prov)
    r = SemanticReplace().evaluate(c, SemanticReplace.Params(threshold=0.9))
    assert r.disposition == "REVIEW"


def test_semantic_replace_unsignaled_allowed_when_flag_on():
    top = Candidate(document_id="old1", content_hash="h", embedding=[1.0, 0.0], content="old")
    prov = FakeProvider(verdicts=[AdjudicatorVerdict(relationship="SUPERSEDES", confidence="HIGH")])
    c = ctx("new", {"top_candidate": top, "top_sim": 0.95}, provider=prov)
    r = SemanticReplace().evaluate(c, SemanticReplace.Params(threshold=0.9, allow_unsignaled_replace=True))
    assert r.disposition == "REPLACE"


def test_semantic_replace_low_confidence_reviews():
    top = Candidate(document_id="old1", content_hash="h", embedding=[1.0, 0.0], content="old")
    prov = FakeProvider(verdicts=[AdjudicatorVerdict(relationship="SUPERSEDES", confidence="LOW")])
    c = ctx("new", {"top_candidate": top, "top_sim": 0.95}, provider=prov, metadata={"supersedes": "old1"})
    assert SemanticReplace().evaluate(c, SemanticReplace.Params(threshold=0.9)).disposition == "REVIEW"


def test_semantic_replace_no_candidate_passes():
    assert SemanticReplace().evaluate(ctx("new", {}), SemanticReplace.Params()).disposition == "PASS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_rules_update_replace.py -q`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Append the Update/Replace section to `llmwiki/rules/palette.py`**

```python
# ---------- Update / Replace ----------

def _direction_signal(ctx, candidate) -> bool:
    """v1 direction signal: an explicit `supersedes` hint naming this candidate.
    (Recency/source-authority require stored timestamps/authority -> deferred to SP-A/SP-B.)"""
    md = ctx.doc.metadata or {}
    hint = md.get("supersedes")
    return hint is not None and (hint == candidate.document_id or hint is True)


class VersionOnChange:
    id = "version_on_change"; category = "update_replace"; kind = "deterministic"

    class Params(BaseModel):
        pass

    def evaluate(self, ctx: EvalContext, params: "VersionOnChange.Params") -> RuleResult:
        match = ctx.findings.get("match")
        if match is None:
            return RuleResult("PASS")
        return RuleResult("UPDATE", {"rule": self.id, "document_id": match.document_id})


class SemanticReplace:
    id = "semantic_replace"; category = "update_replace"; kind = "semantic"

    class Params(BaseModel):
        threshold: float = 0.90
        on_uncertain: Literal["REVIEW"] = "REVIEW"
        allow_unsignaled_replace: bool = False

    def evaluate(self, ctx: EvalContext, params: "SemanticReplace.Params") -> RuleResult:
        cand = ctx.findings.get("top_candidate")
        top_sim = ctx.findings.get("top_sim", 0.0)
        if cand is None or top_sim < params.threshold:
            return RuleResult("PASS")
        verdict = ctx.provider.adjudicate(ctx.content_hash, cand.content)
        sig = {"rule": self.id, "relationship": verdict.relationship,
               "confidence": verdict.confidence, "document_id": cand.document_id}
        if verdict.relationship != "SUPERSEDES" or verdict.confidence != "HIGH":
            if verdict.relationship == "SUPERSEDES":
                return RuleResult("REVIEW", {**sig, "why": "lowconf"})
            return RuleResult("PASS", sig)
        if _direction_signal(ctx, cand) or params.allow_unsignaled_replace:
            return RuleResult("REPLACE", sig)
        return RuleResult(params.on_uncertain, {**sig, "why": "no direction signal"})
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/unit/test_rules_update_replace.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add llmwiki/rules/palette.py tests/unit/test_rules_update_replace.py
git commit -m "feat: update/replace rule primitives (version_on_change, semantic_replace)"
```

---

### Task 6: Routing primitives + register all

**Files:**
- Modify: `llmwiki/rules/palette.py` (append routing + registration)
- Modify: `llmwiki/rules/__init__.py` (import palette so registration runs)
- Test: `tests/unit/test_rules_routing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rules_routing.py
from llmwiki.rules.palette import ConfidenceRoute, Accept
from llmwiki.rules.base import EvalContext, get_rule, known_rules
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.models import IncomingDocument


def ctx(findings=None):
    c = EvalContext(doc=IncomingDocument(collection="kb", content="body"),
                    provider=FakeProvider(), config=CollectionConfig(), candidates_loader=lambda: [])
    c.findings.update(findings or {})
    return c


def test_accept_is_terminal():
    assert Accept().evaluate(ctx(), Accept.Params()).disposition == "ACCEPT"


def test_confidence_route_reviews_on_low():
    c = ctx({"confidence": "LOW"})
    r = ConfidenceRoute().evaluate(c, ConfidenceRoute.Params(min_confidence="HIGH", on_low="REVIEW"))
    assert r.disposition == "REVIEW"
    c2 = ctx({"confidence": "HIGH"})
    assert ConfidenceRoute().evaluate(c2, ConfidenceRoute.Params(min_confidence="HIGH")).disposition == "PASS"


def test_all_builtin_rules_registered():
    for rid in ["min_length", "content_type", "regex_denylist", "knowledge_worthiness",
                "exact_duplicate", "identity_match", "semantic_duplicate",
                "version_on_change", "semantic_replace", "confidence_route", "accept"]:
        assert rid in known_rules()
        assert get_rule(rid).id == rid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_rules_routing.py -q`
Expected: FAIL — `ImportError`/registration missing

- [ ] **Step 3: Append routing + registration to `llmwiki/rules/palette.py`**

```python
# ---------- Routing ----------

_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


class ConfidenceRoute:
    id = "confidence_route"; category = "routing"; kind = "deterministic"

    class Params(BaseModel):
        min_confidence: Literal["HIGH", "MEDIUM", "LOW"] = "HIGH"
        on_low: Literal["REVIEW", "REJECT"] = "REVIEW"

    def evaluate(self, ctx: EvalContext, params: "ConfidenceRoute.Params") -> RuleResult:
        conf = ctx.findings.get("confidence")
        if conf is not None and _ORDER.get(conf, 2) < _ORDER[params.min_confidence]:
            return RuleResult(params.on_low, {"rule": self.id, "confidence": conf})
        return RuleResult("PASS")


class Accept:
    id = "accept"; category = "routing"; kind = "deterministic"

    class Params(BaseModel):
        pass

    def evaluate(self, ctx: EvalContext, params: "Accept.Params") -> RuleResult:
        return RuleResult("ACCEPT", {"rule": self.id})


# ---------- Registration ----------

from llmwiki.rules.base import register_rule as _register

for _rule in (MinLength(), ContentType(), RegexDenylist(), KnowledgeWorthiness(),
              ExactDuplicate(), IdentityMatch(), SemanticDuplicate(),
              VersionOnChange(), SemanticReplace(), ConfidenceRoute(), Accept()):
    _register(_rule)
```

- [ ] **Step 4: Ensure palette is imported so registration runs — update `llmwiki/rules/__init__.py`**

```python
from llmwiki.rules.base import (Candidate, EvalContext, RuleResult, Disposition,
                                cosine, register_rule, get_rule, known_rules)
from llmwiki.rules import palette  # noqa: F401  (imports register the built-in rules)

__all__ = ["Candidate", "EvalContext", "RuleResult", "Disposition", "cosine",
           "register_rule", "get_rule", "known_rules", "palette"]
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/unit/test_rules_routing.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add llmwiki/rules/palette.py llmwiki/rules/__init__.py tests/unit/test_rules_routing.py
git commit -m "feat: routing primitives + register built-in palette"
```

---

### Task 7: Rule engine

**Files:**
- Create: `llmwiki/rules/engine.py`
- Modify: `llmwiki/rules/__init__.py` (export engine)
- Test: `tests/unit/test_rule_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rule_engine.py
from llmwiki.rules.engine import evaluate_pipeline, build_pipeline, EngineDecision
from llmwiki.rules.base import EvalContext, Candidate
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.models import IncomingDocument


def ctx(content, candidates=None, provider=None, declared_id=None):
    return EvalContext(doc=IncomingDocument(collection="kb", content=content, declared_id=declared_id),
                       provider=provider or FakeProvider(), config=CollectionConfig(),
                       candidates_loader=lambda: candidates or [])


PIPE = [
    {"gate": "validity", "rules": [{"type": "min_length", "params": {"min_chars": 40}}]},
    {"gate": "dedup", "rules": [{"type": "exact_duplicate"}, {"type": "semantic_duplicate"}]},
    {"gate": "update", "rules": [{"type": "version_on_change"}]},
]


def test_short_content_rejected_first_gate():
    d = evaluate_pipeline(build_pipeline(PIPE), ctx("too short"))
    assert d.disposition == "REJECT"


def test_no_match_accepts():
    d = evaluate_pipeline(build_pipeline(PIPE), ctx("a genuinely long enough body of text here now"))
    assert d.disposition == "ACCEPT"


def test_exact_duplicate_short_circuits():
    c = ctx("a genuinely long enough body of text here now")
    cand = Candidate(document_id="d1", content_hash=c.content_hash, embedding=[1.0, 0.0])
    c2 = ctx("a genuinely long enough body of text here now", candidates=[cand])
    d = evaluate_pipeline(build_pipeline(PIPE), c2)
    assert d.disposition == "DUPLICATE" and d.document_id == "d1"


def test_context_flow_existence_to_update():
    # identity match with different content: existence annotates -> update fires UPDATE
    text = "a genuinely long enough body of text here now"
    cand = Candidate(document_id="d1", content_hash="old", embedding=[1.0, 0.0], identity_match=True)
    pipe = [
        {"gate": "dedup", "rules": [{"type": "identity_match"}]},
        {"gate": "update", "rules": [{"type": "version_on_change"}]},
    ]
    d = evaluate_pipeline(build_pipeline(pipe), ctx(text, candidates=[cand], declared_id="doc-1"))
    assert d.disposition == "UPDATE" and d.document_id == "d1"


def test_build_pipeline_unknown_rule_raises():
    import pytest
    with pytest.raises(ValueError):
        build_pipeline([{"gate": "g", "rules": [{"type": "nope"}]}])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_rule_engine.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `llmwiki/rules/engine.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from llmwiki.rules.base import EvalContext, Disposition, get_rule


@dataclass
class CompiledRule:
    rule: object
    params: object


@dataclass
class CompiledGate:
    name: str
    rules: list[CompiledRule]


@dataclass
class EngineDecision:
    disposition: Disposition
    document_id: str | None = None
    reason: str = ""
    signals: dict = field(default_factory=dict)


def build_pipeline(pipeline_config: list[dict]) -> list[CompiledGate]:
    gates: list[CompiledGate] = []
    for gate in pipeline_config:
        compiled: list[CompiledRule] = []
        for rule_cfg in gate.get("rules", []):
            rule = get_rule(rule_cfg["type"])          # raises ValueError on unknown
            params = rule.Params(**rule_cfg.get("params", {}))
            compiled.append(CompiledRule(rule=rule, params=params))
        gates.append(CompiledGate(name=gate.get("gate", ""), rules=compiled))
    return gates


def evaluate_pipeline(gates: list[CompiledGate], ctx: EvalContext) -> EngineDecision:
    for gate in gates:
        for cr in gate.rules:
            result = cr.rule.evaluate(ctx, cr.params)
            if result.context_updates:
                ctx.findings.update(result.context_updates)
            if result.disposition != "PASS":
                return EngineDecision(disposition=result.disposition,
                                      document_id=result.signals.get("document_id"),
                                      reason=f"{gate.name}:{cr.rule.id}",
                                      signals=result.signals)
    return EngineDecision(disposition="ACCEPT", reason="default-accept")
```

- [ ] **Step 4: Export the engine — update `llmwiki/rules/__init__.py`** (append to imports + `__all__`)

```python
from llmwiki.rules.engine import build_pipeline, evaluate_pipeline, EngineDecision
```
Add `"build_pipeline", "evaluate_pipeline", "EngineDecision"` to `__all__`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/unit/test_rule_engine.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add llmwiki/rules/engine.py llmwiki/rules/__init__.py tests/unit/test_rule_engine.py
git commit -m "feat: rule engine (build_pipeline + first-match short-circuit evaluate)"
```

---

### Task 8: Config — `pipeline` field + default pipeline

**Files:**
- Modify: `llmwiki/config.py`
- Test: `tests/unit/test_config.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/unit/test_config.py
from llmwiki.config import default_pipeline


def test_default_pipeline_reproduces_current_gates():
    cfg = CollectionConfig()
    pipe = cfg.pipeline or default_pipeline(cfg)
    gate_names = [g["gate"] for g in pipe]
    assert gate_names == ["validity", "dedup", "update"]
    dedup_types = [r["type"] for r in pipe[1]["rules"]]
    assert dedup_types == ["exact_duplicate", "identity_match", "semantic_duplicate"]
    # semantic_replace is NOT in the default pipeline (opt-in)
    assert all(r["type"] != "semantic_replace" for g in pipe for r in g["rules"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config.py::test_default_pipeline_reproduces_current_gates -q`
Expected: FAIL — `ImportError: default_pipeline`

- [ ] **Step 3: Add `pipeline` + `default_pipeline` to `llmwiki/config.py`**

Add a field to `CollectionConfig` (after `knowledge_rubric`):

```python
    pipeline: list[dict] | None = None
```

Add this module-level function at the end of `config.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/unit/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llmwiki/config.py tests/unit/test_config.py
git commit -m "feat: pipeline config field + default pipeline factory"
```

---

### Task 9: Refactor `IngestService` onto the engine (+ REPLACE)

**Files:**
- Modify: `llmwiki/pipeline.py`, `llmwiki/storage/index_store.py` (add `mark_replaced`)
- Test: `tests/integration/test_pipeline_parity.py`, and update `tests/integration/test_pipeline.py`, `test_coordinator.py`, `test_pipeline_quality.py`

- [ ] **Step 1: Write the failing parity + replace test**

```python
# tests/integration/test_pipeline_parity.py
from llmwiki.pipeline import IngestService
from llmwiki.storage import IndexStore
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import AdjudicatorVerdict
from llmwiki.models import IncomingDocument, Outcome


def svc(tmp_path, provider=None):
    s = IngestService(IndexStore(str(tmp_path / "i.db")), str(tmp_path / "r"),
                      provider or FakeProvider())
    s.ensure_collection("kb")
    return s


def ing(s, content, **kw):
    return s.ingest(IncomingDocument(collection="kb", content=content, **kw))


def test_parity_new_dup_update_reject_review(tmp_path):
    s = svc(tmp_path)
    long = "The enterprise refund window is thirty days from the invoice date."
    assert ing(s, long, declared_id="d1").outcome is Outcome.NEW
    assert ing(s, long, declared_id="d1").outcome is Outcome.DUPLICATE
    assert ing(s, long + " Updated terms apply.", declared_id="d1").outcome is Outcome.UPDATE
    assert ing(s, "ok thanks").outcome is Outcome.REJECTED          # min_length


def test_replace_via_supersedes_hint(tmp_path):
    # custom pipeline that enables semantic_replace
    s = svc(tmp_path, provider=FakeProvider(
        verdicts=[AdjudicatorVerdict(relationship="SUPERSEDES", confidence="HIGH")]))
    s.index.set_collection_config("kb", {"pipeline": [
        {"gate": "dedup", "rules": [{"type": "semantic_duplicate",
                                     "params": {"threshold_high": 0.99, "gray_band": 0.0}}]},
        {"gate": "update", "rules": [{"type": "semantic_replace",
                                      "params": {"threshold": 0.0}}]},
    ]})
    old = ing(s, "The old refund policy is sixty days.", declared_id="p-old")
    assert old.outcome is Outcome.NEW
    rec = ing(s, "The refund policy is now thirty days, replacing the old one.",
              metadata={"supersedes": old.document_id})
    assert rec.outcome is Outcome.REPLACE
    # old doc archived + linked
    assert s.index.get_document(old.document_id).status == "replaced"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_pipeline_parity.py -q`
Expected: FAIL — REPLACE not handled / `status` attribute missing

- [ ] **Step 3: Add `status` + `replaced_by`/`replaces` to the document model and `mark_replaced` to the store**

In `llmwiki/models.py`, add fields to `Document` (after `wiki_path`):

```python
    status: str = "active"          # active | replaced
    replaced_by: str | None = None
    replaces: str | None = None
```

In `llmwiki/storage/index_store.py`: extend the `documents` table + `save_document`/`_row_to_doc` to persist the new columns, and add `mark_replaced`. Update `_SCHEMA` documents table:

```sql
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, collection TEXT NOT NULL, stable_identity TEXT NOT NULL,
  current_version_id TEXT, wiki_path TEXT, created_at TEXT, updated_at TEXT,
  status TEXT NOT NULL DEFAULT 'active', replaced_by TEXT, replaces TEXT,
  UNIQUE(collection, stable_identity));
```

Replace `save_document`, `_row_to_doc`, and add `mark_replaced`:

```python
    def save_document(self, doc: Document) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO documents
               (id, collection, stable_identity, current_version_id, wiki_path,
                created_at, updated_at, status, replaced_by, replaces)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (doc.id, doc.collection, doc.stable_identity, doc.current_version_id,
             doc.wiki_path, doc.created_at.isoformat(), doc.updated_at.isoformat(),
             doc.status, doc.replaced_by, doc.replaces))
        self._conn.commit()

    def mark_replaced(self, old_id: str, new_id: str) -> None:
        self._conn.execute(
            "UPDATE documents SET status='replaced', replaced_by=? WHERE id=?", (new_id, old_id))
        self._conn.commit()

    def _row_to_doc(self, row) -> Document:
        from datetime import datetime
        keys = row.keys()
        return Document(
            id=row["id"], collection=row["collection"], stable_identity=row["stable_identity"],
            current_version_id=row["current_version_id"], wiki_path=row["wiki_path"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            status=row["status"] if "status" in keys else "active",
            replaced_by=row["replaced_by"] if "replaced_by" in keys else None,
            replaces=row["replaces"] if "replaces" in keys else None)
```

Also exclude replaced docs from `current_candidates` (add `AND d.status='active'` to its WHERE clause):

```python
               WHERE d.collection=? AND d.status='active'""", (collection,)).fetchall()
```

- [ ] **Step 4: Rewrite `IngestService.ingest` + `_apply` to use the engine**

Replace the imports block and the `ingest`/`_record_gate_block`/`_apply` methods in `llmwiki/pipeline.py`:

```python
from __future__ import annotations
import uuid
from llmwiki.config import CollectionConfig, default_pipeline
from llmwiki.models import (IncomingDocument, Document, DocumentVersion,
                            DecisionRecord, ReviewItem, Outcome)
from llmwiki.providers.base import Provider
from llmwiki.rules.base import EvalContext
from llmwiki.rules.engine import build_pipeline, evaluate_pipeline
from llmwiki.storage import IndexStore, ContentStore
from llmwiki.text import content_hash, shingles


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _wiki_page(doc_id: str, text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else doc_id
    return f"# {first[:80]}\n\n{text.strip()}\n"


_TERMINAL_OUTCOME = {
    "ACCEPT": Outcome.NEW, "UPDATE": Outcome.UPDATE, "REPLACE": Outcome.REPLACE,
    "DUPLICATE": Outcome.DUPLICATE, "REJECT": Outcome.REJECTED, "REVIEW": Outcome.NEEDS_REVIEW,
}


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

    def _load_config(self, collection: str) -> CollectionConfig:
        row = self.index.get_collection(collection)
        stored = row.get("config") if row else None
        return CollectionConfig(**stored) if stored else self.config

    def _build_context(self, doc: IncomingDocument, cfg: CollectionConfig) -> EvalContext:
        identity = doc.declared_id or doc.source_uri

        def loader():
            cands = self.index.current_candidates(doc.collection)
            if identity:
                existing = self.index.find_by_identity(doc.collection, identity)
                for c in cands:
                    if existing and c.document_id == existing.id:
                        c.identity_match = True
            return cands

        return EvalContext(doc=doc, provider=self.provider, config=cfg, candidates_loader=loader)

    def ingest(self, doc: IncomingDocument, principal_id: str | None = None) -> DecisionRecord:
        if doc.idempotency_key:
            prior = self.index.get_decision_by_idempotency(doc.collection, doc.idempotency_key)
            if prior is not None:
                return prior

        cfg = self._load_config(doc.collection)
        pipe = build_pipeline(cfg.pipeline or default_pipeline(cfg))
        ctx = self._build_context(doc, cfg)
        decision = evaluate_pipeline(pipe, ctx)

        outcome = _TERMINAL_OUTCOME[decision.disposition]
        rec = DecisionRecord(id=_id("dec"), collection=doc.collection, outcome=outcome,
                             content_hash=ctx.content_hash, principal_id=principal_id,
                             document_id=decision.document_id, reason=decision.reason,
                             signals=decision.signals)

        if outcome in (Outcome.NEW, Outcome.UPDATE, Outcome.REPLACE):
            return self._apply(doc, outcome, rec, ctx, principal_id)
        if outcome is Outcome.NEEDS_REVIEW:
            self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
            self.index.save_review(ReviewItem(
                id=_id("rev"), decision_id=rec.id, collection=doc.collection,
                candidates=[{"document_id": decision.document_id, **decision.signals}]))
            return rec
        self.index.save_decision(rec, idempotency_key=doc.idempotency_key)  # DUPLICATE / REJECTED
        return rec

    def _apply(self, doc, outcome, rec, ctx, principal_id):
        cs = ContentStore(self.content_root, doc.collection)
        identity = ctx.identity
        chash = ctx.content_hash
        if outcome in (Outcome.NEW, Outcome.REPLACE):
            doc_id = _id("doc")
            replaces = rec.document_id if outcome is Outcome.REPLACE else None
            self.index.save_document(Document(id=doc_id, collection=doc.collection,
                                              stable_identity=identity or chash,
                                              wiki_path=f"wiki/{doc_id}.md", replaces=replaces))
            if outcome is Outcome.REPLACE and replaces:
                self.index.mark_replaced(replaces, doc_id)
        else:  # UPDATE
            doc_id = rec.document_id

        version_id = _id("ver")
        commit = cs.write_document(doc_id=doc_id, source_text=doc.content,
                                   wiki_text=_wiki_page(doc_id, doc.content),
                                   log_line=f"{outcome.value} {doc_id} {chash[:8]}")
        self.index.save_version(DocumentVersion(id=version_id, document_id=doc_id,
                                                content_hash=chash, git_commit=commit,
                                                submitter_id=principal_id),
                                embedding=ctx.embedding, shingles=ctx.shingles, content=doc.content)
        self.index.set_current_version(doc_id, version_id)
        rec.document_id = doc_id
        rec.resulting_version_id = version_id
        self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
        return rec
```

- [ ] **Step 5: Update the dedup-isolation tests to use a pipeline instead of `quality_enabled`**

In `tests/integration/test_pipeline.py` and `tests/integration/test_coordinator.py`, the helper passed `config=CollectionConfig(quality_enabled=False)`. Replace that with a dedup-only pipeline (no validity gate):

```python
DEDUP_ONLY = CollectionConfig(pipeline=[
    {"gate": "dedup", "rules": [{"type": "exact_duplicate"}, {"type": "identity_match"},
                                {"type": "semantic_duplicate"}]},
    {"gate": "update", "rules": [{"type": "version_on_change"}]},
])
```
and pass `config=DEDUP_ONLY` where `CollectionConfig(quality_enabled=False)` was used. (`test_pipeline_quality.py` keeps the default pipeline, which still filters short junk — those assertions are unchanged.)

- [ ] **Step 6: Run the affected tests**

Run: `.venv/bin/pytest tests/integration/test_pipeline_parity.py tests/integration/test_pipeline.py tests/integration/test_coordinator.py tests/integration/test_pipeline_quality.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add llmwiki/pipeline.py llmwiki/models.py llmwiki/storage/index_store.py tests/integration/
git commit -m "refactor: IngestService runs the rule engine; add REPLACE apply"
```

---

### Task 10: API — validate `pipeline` config

**Files:**
- Modify: `llmwiki/api/routers.py`
- Test: `tests/integration/test_collection_config.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/integration/test_collection_config.py
def test_put_pipeline_config_validates(tmp_path):
    c = make_client(tmp_path)
    good = {"pipeline": [{"gate": "validity", "rules": [{"type": "min_length", "params": {"min_chars": 10}}]}]}
    assert c.put("/v1/collections/kb/config", json=good, headers=A).status_code == 200
    # unknown rule type -> 422
    bad = {"pipeline": [{"gate": "g", "rules": [{"type": "nope"}]}]}
    assert c.put("/v1/collections/kb/config", json=bad, headers=A).status_code == 422
    # invalid params for a known rule -> 422
    badp = {"pipeline": [{"gate": "g", "rules": [{"type": "min_length", "params": {"min_chars": "lots"}}]}]}
    assert c.put("/v1/collections/kb/config", json=badp, headers=A).status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_collection_config.py::test_put_pipeline_config_validates -q`
Expected: FAIL — invalid pipeline accepted (200 instead of 422)

- [ ] **Step 3: Extend `_validate_and_dump_config` in `llmwiki/api/routers.py`**

Add pipeline validation. Update the imports and the helper:

```python
from llmwiki.rules.engine import build_pipeline
```

In `_validate_and_dump_config`, after the existing regex validation and before `return cfg.model_dump()`:

```python
    if cfg.pipeline is not None:
        try:
            build_pipeline(cfg.pipeline)   # resolves rule types + validates each rule's params
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"invalid pipeline: {e}")
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"invalid pipeline params: {e}")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/integration/test_collection_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llmwiki/api/routers.py tests/integration/test_collection_config.py
git commit -m "feat: validate pipeline config at config-set time (422 on bad rules)"
```

---

### Task 11: Remove superseded code, full suite, README

**Files:**
- Remove: `llmwiki/classifier.py`, `llmwiki/gates/` (and `tests/unit/test_classifier.py`, `tests/unit/test_gates.py`)
- Modify: `README.md`
- Test: full suite

- [ ] **Step 1: Confirm nothing imports the old modules**

Run: `grep -rn "from llmwiki.classifier\|llmwiki.gates\|build_chain\|GateContext" llmwiki/ | grep -v "rules/"`
Expected: no results (the only references were in `pipeline.py`, now refactored).

- [ ] **Step 2: Remove the superseded modules + their tests**

```bash
git rm llmwiki/classifier.py tests/unit/test_classifier.py
git rm -r llmwiki/gates tests/unit/test_gates.py
```

- [ ] **Step 3: Run the entire suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all green)

- [ ] **Step 4: Update `README.md`** — replace the "Quality-control gates" section with a "Composable gates & rules" section:

```markdown
## Composable gates & rules

A collection's decision pipeline is a configurable list of **gates**, each containing **rules**
from a categorized palette. Rules are evaluated in order, first-match within a gate, and the first
gate to fire a terminal disposition short-circuits the chain (otherwise the doc is ACCEPTed).

Categories & built-in rule types:
- **Validity** — `min_length`, `content_type`, `regex_denylist`, `knowledge_worthiness`
- **Existence** — `exact_duplicate`, `identity_match`, `semantic_duplicate`
- **Update/Replace** — `version_on_change`, `semantic_replace`
- **Routing** — `confidence_route`, `accept`

Set a collection's pipeline (admin only):

    curl -X PUT localhost:8000/v1/collections/kb/config -H "authorization: Bearer $KEY" \
      -H 'content-type: application/json' \
      -d '{"pipeline":[
            {"gate":"validity","rules":[{"type":"min_length","params":{"min_chars":40}}]},
            {"gate":"dedup","rules":[{"type":"exact_duplicate"},{"type":"semantic_duplicate"}]},
            {"gate":"update","rules":[{"type":"version_on_change"}]}]}'

With no pipeline configured, a default pipeline reproduces the built-in behavior. `semantic_replace`
(supersede a different existing doc) is opt-in and review-by-default — it only auto-replaces with a
direction signal (a `supersedes` metadata hint) or when `allow_unsignaled_replace` is set.
```

- [ ] **Step 5: Final full-suite run + commit**

Run: `.venv/bin/pytest -q`
Expected: PASS

```bash
git add -A
git commit -m "refactor: remove superseded classifier/gates; document composable rules"
```

---

## Self-Review Notes (plan vs. spec)

- **Gate contains categorized rules; first-match short-circuit + shared context** → Task 2 (EvalContext/findings), Task 7 (engine). ✅
- **4 categories + palette** → Tasks 3–6 (validity/existence/update-replace/routing). ✅
- **Semantic rules via thresholds + confidence→REVIEW; provider error→REVIEW** → Task 3 (`knowledge_worthiness`), Task 4 (`semantic_duplicate`), Task 5 (`semantic_replace`). ✅
- **Parameterized palette, registry for new types** → Task 2 (registry), Task 6 (registration), `get_rule` raises on unknown. ✅
- **semantic_replace: hybrid retrieve + SUPERSEDES + direction signal, review-by-default, opt-in unsignaled; archive+link, never delete** → Task 1 (SUPERSEDES), Task 5 (logic), Task 9 (`mark_replaced`, `replaces`/`replaced_by`, exclude replaced from candidates). ✅
- **Config `pipeline` + default reproduces behavior; 422 on bad config** → Task 8 (default_pipeline), Task 10 (validation). ✅
- **Refactor existing logic into primitives; preserve outcomes (parity)** → Task 9 (parity test), Task 11 (remove old modules). ✅
- **Outcome.REPLACE added; DecisionRecord/review unchanged** → Task 1, Task 9. ✅
- **Type consistency:** `Disposition` vocabulary used identically in base/palette/engine; `RuleResult(disposition, signals, context_updates)`; `EngineDecision(disposition, document_id, reason, signals)`; rules expose `id`/`category`/`kind`/`Params`/`evaluate`; `findings["match"]` (same-doc→UPDATE) vs `findings["top_candidate"]` (nearby→REPLACE) used consistently across Tasks 4/5/7/9. ✅
- **Scoping note:** v1 direction signal = explicit `supersedes` hint; recency/source-authority deferred (need stored timestamps/authority — SP-A/SP-B). Documented in Task 5 and the spec's §7 possibilities. 
```
