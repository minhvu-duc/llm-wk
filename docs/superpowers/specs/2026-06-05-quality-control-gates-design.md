# LLM-Wiki: Quality-Control Gates — Design

**Date:** 2026-06-05
**Status:** Approved (brainstorming complete)
**Scope:** Subsystem A — a pluggable quality-control gate chain that filters incoming content
for *knowledge-worthiness* before dedup/versioning. Subsystem B (fact-merging into topic pages)
is a **separate future cycle** and is out of scope here.

## 1. Summary

The existing ingest engine decides duplicate/update/new and gates on **source trust**, but it
does **not** judge whether content is *worth storing*. For the target use case — an LLM chat
agent that summarizes each conversation and sends it to the wiki to accumulate knowledge — this
means greetings, scheduling chit-chat, ephemeral/transactional details, and PII flow straight in
as `NEW` pages.

This subsystem adds a **pluggable chain of quality gates** that runs after the trust gate and
**before** fingerprinting/embedding. Each gate is a small, independent unit that returns
`PASS` / `REJECT` / `REVIEW`. Clear non-knowledge is dropped (logged + auditable), borderline
content goes to the existing human review queue, and real knowledge proceeds to the unchanged
dedup/version flow. Rules are **user-defined per collection** (no code required).

## 2. Goals & Non-Goals

### Goals
- Filter incoming content for knowledge-worthiness before it costs an embedding or a stored page.
- Make quality rules **operator-configurable per collection** (the explicit requirement).
- Keep gates small, independent, and reorderable/removable (a pluggable chain).
- Reuse existing outcomes (`REJECTED`, `NEEDS_REVIEW`), the `DecisionRecord` log, and the review queue.
- Never silently drop possible knowledge (asymmetric caution + fail-safe).

### Non-Goals
- Fact-merging / topic-page synthesis (subsystem B, future cycle).
- Truth/fact verification against the world (we judge *worth storing*, not *is it factually true*).
- Changing the dedup/version classifier.

## 3. Core Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Build order | Quality gate (A) first, fact-merging (B) as a separate later cycle. |
| Architecture | **Pluggable chain of gates**, runs after authz, before fingerprint/embed. |
| What is "knowledge" | **User-defined per collection** (not a hardcoded taxonomy). |
| Rule form | **Structured deterministic rules + an LLM natural-language rubric.** |
| On rejection | **Confidence-based routing**: clear non-knowledge → `REJECTED` (logged); borderline → `NEEDS_REVIEW`; clear knowledge → proceed. |
| Outcome model | **Reuse `REJECTED`** with `signals.gate` + `signals.reason` (no new enum value). |

## 4. Pipeline Integration

The chain runs inside `IngestService.ingest()`, after the idempotency check, **before** any
embedding:

```
authn/authz (router) → coordinator → ingest()
   → [idempotency check]
   → GATE CHAIN
        ├─ PASS   → fingerprint → classify (dedup/version) → apply → record   (existing flow)
        ├─ REJECT → DecisionRecord(REJECTED, signals={gate, reason, ...}) → save → return
        └─ REVIEW → DecisionRecord(NEEDS_REVIEW, signals={gate, ...}) → save + queue → return
```

Running before `provider.embed()` means rejected content costs zero embedding/storage. The
chain is skipped entirely when `quality_enabled` is false (default true).

## 5. Components

### 5.1 Gate interface (`llmwiki/gates/base.py`)
```python
class GateVerdict(BaseModel):
    decision: Literal["PASS", "REJECT", "REVIEW"]
    gate: str
    reason: str = ""
    signals: dict = {}

@dataclass
class GateContext:
    config: CollectionConfig
    provider: Provider

class Gate(Protocol):
    name: str
    def check(self, doc: IncomingDocument, ctx: GateContext) -> GateVerdict: ...
```
Gates are pure relative to their inputs (config + provider via context) and individually
unit-testable.

### 5.2 Built-in gates (`llmwiki/gates/builtin.py`), default order
1. **`MinInfoGate`** (deterministic) — `REJECT` if normalized length < `min_chars`.
2. **`DenylistGate`** (deterministic) — if any `denylist_patterns` regex matches → `denylist_action`
   (`REJECT` or `REVIEW`). PII/secret screen.
3. **`KnowledgeWorthinessGate`** (LLM) — calls `provider.assess(text, rubric)` and routes by
   confidence (see 5.4).

### 5.3 Gate chain (`llmwiki/gates/chain.py`)
- `GateChain(gates: list[Gate])` with `run(doc, ctx) -> GateVerdict | None`.
- Runs gates in order, returns the **first non-`PASS`** verdict, or `None` if all pass.
- `build_chain(config) -> GateChain` constructs the chain from `config.gate_order`, resolving
  names via a gate registry. Cheap deterministic gates ordered before the LLM gate so obvious
  junk short-circuits before an LLM call.

### 5.4 Assessor contract (extend `Provider`)
```python
class KnowledgeVerdict(BaseModel):
    is_knowledge: bool
    category: str = ""
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    rationale: str = ""

def assess(self, text: str, rubric: str) -> KnowledgeVerdict: ...
```
**Routing in `KnowledgeWorthinessGate`:**
- `is_knowledge=True` → `PASS`
- `is_knowledge=False` + `HIGH` → `REJECT`
- `is_knowledge=False` + `MEDIUM`/`LOW` → `REVIEW`
- `assess` raises / unparseable → `REVIEW` (fail-safe)

`FakeProvider.assess` is deterministic (short or greeting-like text → not knowledge) for tests.
`LiteLLMProvider.assess` sends the rubric + text, parses structured JSON, and fail-safes to a
`LOW` verdict on unparseable output (same pattern as the existing adjudicator).

## 6. Rule-Setting (per-collection config)

**Existing gap addressed:** today `IngestService` uses a single global `CollectionConfig` and
ignores the per-collection config that `create_collection` already persists. This subsystem wires
per-collection config into the decision flow so rules are actually settable.

- `IngestService` loads the target collection's `CollectionConfig` from the index store at ingest
  time (falling back to defaults) instead of a single global config.
- **REST (admin-only via existing authz):**
  - `POST /v1/collections` `{name, config?}` — create with optional rules
  - `GET /v1/collections/{c}/config` — read current rules
  - `PUT /v1/collections/{c}/config` — update rules
- Operators set, per collection: `knowledge_rubric` (what counts as knowledge), `gate_order`
  (which gates and order), `min_chars`, `denylist_patterns`, `denylist_action`, `quality_enabled` —
  all without code. Custom gates register a name and become usable in `gate_order`.

### `CollectionConfig` additions
```python
quality_enabled: bool = True
gate_order: list[str] = ["min_info", "denylist", "knowledge"]
min_chars: int = 40
denylist_patterns: list[str] = []
denylist_action: Literal["REJECT", "REVIEW"] = "REVIEW"
knowledge_rubric: str = (
    "Keep durable facts, decisions/resolutions, reusable how-to steps, and persistent "
    "user/entity preferences or attributes. Drop greetings, small talk, scheduling chatter, "
    "and ephemeral or purely transactional exchanges with no lasting value."
)
```

## 7. Error Handling

- `denylist_patterns` are compiled and **validated when rules are set** → invalid regex returns
  `422`, never a runtime failure.
- LLM `assess` failure (timeout, unparseable) → `REVIEW` (never a silent drop or silent pass).
- Gates run before embedding, so rejects incur no embedding/storage cost.
- Gate decisions honor the existing `idempotency_key` (a re-sent rejected item returns the
  original decision).
- Setting config on a non-admin principal → `403`; unknown collection → `404`.

## 8. Testing

- **Per-gate units:** `MinInfoGate` (short → REJECT, ok → PASS); `DenylistGate` (match → action,
  no match → PASS); `KnowledgeWorthinessGate` with a fake assessor (knowledge → PASS,
  clear-noise+HIGH → REJECT, borderline MEDIUM/LOW → REVIEW, provider error → REVIEW).
- **Chain:** order respected; first non-PASS short-circuits (later gates not run); all-pass → None.
- **Assessor:** `FakeProvider.assess` deterministic; `LiteLLMProvider.assess` parse + fail-safe
  (mocked, no network).
- **Pipeline integration:** junk summary → `REJECTED` with `provider.embed` asserted **not**
  called; borderline → `NEEDS_REVIEW` + review item created; real knowledge → `NEW` (existing flow).
- **Per-collection config:** two collections with different rubrics/thresholds produce different
  outcomes for the same input; config persists and reloads.
- **Config API:** set rules; invalid regex → `422`; non-admin → `403`.
- **REST contract:** ingesting junk returns `REJECTED` in the response body.

## 9. Files

**Create:**
- `llmwiki/gates/__init__.py`
- `llmwiki/gates/base.py` — `Gate` protocol, `GateVerdict`, `GateContext`
- `llmwiki/gates/builtin.py` — `MinInfoGate`, `DenylistGate`, `KnowledgeWorthinessGate`
- `llmwiki/gates/chain.py` — `GateChain`, registry, `build_chain(config)`
- `tests/unit/test_gates.py`, `tests/unit/test_knowledge_provider.py`
- `tests/integration/test_pipeline_quality.py`, `tests/integration/test_collection_config.py`

**Modify:**
- `llmwiki/providers/base.py` — add `KnowledgeVerdict` + `assess`
- `llmwiki/providers/fake.py` — add deterministic `assess`
- `llmwiki/providers/litellm_provider.py` — add `assess` (fail-safe parse)
- `llmwiki/config.py` — quality fields
- `llmwiki/pipeline.py` — load per-collection config + run gate chain before fingerprint
- `llmwiki/api/schemas.py` — config request/response models
- `llmwiki/api/routers.py` — `GET`/`PUT` config; `POST /collections` accepts config

## 10. Future (subsystem B, separate cycle)
Fact-merging into topic/entity pages: a clean filtered stream from this gate becomes the input to
LLM-driven synthesis that merges facts into topic pages (instead of `UPDATE` replacing), with
contradiction handling. Its own spec → plan → implementation cycle.
