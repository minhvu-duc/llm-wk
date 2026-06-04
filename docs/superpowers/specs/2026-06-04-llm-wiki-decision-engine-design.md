# LLM-Wiki: Ingest Decision Engine — Design

**Date:** 2026-06-04
**Status:** Approved (brainstorming complete)
**Scope:** v1 — the ingest decision engine. Query and lint operations are explicitly out of scope (future phases).

## 1. Summary

An open-source, enterprise-oriented backend that implements the "LLM Wiki" pattern (Karpathy): instead of re-discovering knowledge per query like RAG, an LLM incrementally builds and maintains a persistent markdown wiki whose maintenance cost is near zero.

This project builds **only the ingest side**: given an incoming document, the system decides whether to **store it as new, update an existing document, reject it as a duplicate, reject it for lacking trust, or escalate it for human review** — then applies that decision to a git-versioned markdown wiki plus an index database.

The novel core is the **decision engine**. Its job is to *make the decision* about whether and how a document enters the store.

No rich UI. The system exposes a **REST API** and an **MCP server** over a shared core, plus a **minimal dashboard** for the human review queue and calibration.

## 2. Goals & Non-Goals

### Goals
- Receive a document, decide its disposition, and persist that decision with a full audit trail.
- Treat the **authenticated submitter identity** as the trust gate.
- Detect duplicates and versions; never silently overwrite distinct documents.
- Be easy to run (`docker run` / `llmwiki serve`) with sane defaults; scale to Postgres for HA.
- Expose REST + MCP over one shared core so both always agree.
- Pluggable LLM/embedding provider, cloud default.

### Non-Goals (v1)
- Query/search-answering and "file answers back as pages" (future).
- Lint operation (contradiction/stale/orphan sweeps) (future).
- Cryptographic document signatures / PKI (trust is identity-based).
- LLM-based authenticity scoring of content.
- Rich end-user UI.

## 3. Core Decisions (from brainstorming)

| Question | Decision |
|---|---|
| What does the engine decide? | Trust gate is the core; dedup + versioning follow it. |
| What establishes trust? | **Authenticated submitter identity** (API key / OIDC / mTLS) → principal → per-collection authorization. |
| How is "same document" identified? | Submitter-provided stable ID/URI when present; else content-hash + semantic similarity + LLM adjudication. |
| LLM/embedding backend | Pluggable provider, **cloud default**, swappable to self-hosted. |
| Language/runtime | **Python** (FastAPI + MCP SDK). |
| Storage | **Files + Git** as the wiki, **SQLite + sqlite-vec** index, pluggable to **Postgres + pgvector**. |
| Confidence gate | **Layered single-pass** (bands + margin + structured adjudicator verdict + conflicting-signal check). Self-consistency `N` configurable, default 1. |
| Git granularity | **One repo per collection**; raw sources in `sources/`, wiki in `wiki/`. |

## 4. The Decision Engine

### 4.1 Decision outcomes

| Outcome | Meaning | Effect |
|---|---|---|
| `REJECTED` | Failed the trust gate — unauthenticated, not authorized for the collection, or policy violation (bad type/oversize). | Nothing stored; decision logged. |
| `DUPLICATE` | Resolves to an existing document **and** content hash matches a stored version. | No-op (idempotent); logged. |
| `UPDATE` | Resolves to an existing document but content differs. | New version stored, wiki page(s) regenerated, diff recorded. |
| `NEW` | No matching identity. | New document + version created, indexed, wiki page(s) generated. |
| `NEEDS_REVIEW` | Ambiguous — no stable ID and similarity in gray band, low adjudication confidence, multiple strong matches, or detected conflict. | Queued for a human; **not** auto-applied. |

### 4.2 Pipeline

Runs per ingest, **serialized per collection** so the decision always observes consistent state.

1. **Authenticate** caller → principal (API key / OIDC / mTLS).
2. **Authorize** for target collection + policy checks (allowed types, max size, required fields). Fail → `REJECTED`.
3. **Extract & normalize** text (md/txt/pdf/html…); capture metadata (submitter, source URI, declared doc_id, timestamps).
4. **Fingerprint** — SHA-256 of normalized text (exact-dup) + chunk & embed (semantic).
5. **Resolve identity** — direct lookup by submitter ID/URI if given; else vector similarity search for nearest existing documents.
6. **Classify** (see 4.3).
7. **Apply** (transactional): create/append version, write raw source + regenerate wiki markdown, re-embed, git-commit.
8. **Record** — write `DecisionRecord` (outcome, all signals, principal, timestamps); append `log.md`; update `index.md`.

### 4.3 Classification & the confidence gate

**Stage 1 — Similarity bands (deterministic, no LLM):**
- `sim < LOW` (default ~0.80) → `NEW`, no LLM call.
- `sim ≥ HIGH` (default ~0.97) → `UPDATE`/`DUPLICATE`, no LLM call.
- `LOW ≤ sim < HIGH` → **gray band** → escalate to adjudicator.
- **Small top-2 margin** (best vs. second-best within `MARGIN`, default ~0.02) → escalate regardless of absolute score.
- Direct ID match: same ID + same hash → `DUPLICATE`; same ID + different content → `UPDATE` (no LLM needed).

**Stage 2 — Adjudicator (gray band only).** Returns a **structured verdict**:
```json
{ "relationship": "SAME_UPDATED | DIFFERENT | RELATED_BUT_DISTINCT | CONFLICTING",
  "confidence": "HIGH | MEDIUM | LOW",
  "rationale": "..." }
```

Route to `NEEDS_REVIEW` when **any** fire ("low confidence" as a concrete OR):
1. self-reported `confidence` is not `HIGH`;
2. `relationship` is `RELATED_BUT_DISTINCT` or `CONFLICTING`;
3. **conflicting signals** — e.g., embedding ≥ HIGH but lexical/shingle overlap low, or LLM says `DIFFERENT` at high similarity;
4. *(optional, when `N>1`)* **self-consistency disagreement** across `N` adjudication samples.

Otherwise: `SAME_UPDATED` + HIGH → `UPDATE`; `DIFFERENT` + HIGH → `NEW`.

**Two safety principles:**
- **Asymmetric caution** — a wrong `UPDATE` silently overwrites/merges distinct docs (hard to detect); a wrong `NEW` only leaves a near-duplicate (recoverable by future lint). On uncertainty we **never auto-`UPDATE`**; we fall to `NEEDS_REVIEW` or `NEW`.
- **Fail safe** — provider/embedding failures never produce a guessed mutation; they route to `NEEDS_REVIEW` (or surface an error), never a silent store/overwrite.

**Calibration.** Every decision logs its scores, votes, and rationale. A dashboard calibration view lets an operator label a sample of past decisions and recomputes recommended `LOW`/`HIGH`/`MARGIN` per collection. Ship sane defaults; collections diverge over time.

## 5. Components

Each unit has one purpose, a narrow interface, and is independently testable.

1. **HTTP API** (FastAPI) — ingest, decision status, doc get/list, collection admin, review-queue actions, `healthz`/`metrics`.
2. **MCP server** — thin adapter exposing the same core as tools: `ingest_document`, `get_document`, `get_decision`, `list_documents`, `list_pending_reviews`, `resolve_review`.
3. **Trust/Auth module** — pluggable `Authenticator` (API key, OIDC, mTLS) → principal; per-collection authorization + ingest policy.
4. **Decision engine (core)** — the §4 pipeline; provider- and storage-injected, no direct I/O (pure, unit-testable with fakes).
5. **LLM provider abstraction** — `embed()` and `adjudicate()`; cloud default, swappable.
6. **Storage layer** — *(a)* **content store**: markdown wiki + raw sources on disk, git-versioned; *(b)* **index store**: SQLite + sqlite-vec → Postgres + pgvector.
7. **Ingest coordinator** — per-collection serialized queue guaranteeing decision consistency; cross-collection runs in parallel.
8. **Dashboard** — minimal web UI: browse collections/docs, read the decision log, act on the review queue, calibration view.

## 6. Interfaces

### 6.1 REST (MCP tools mirror these)
```
POST /v1/collections/{c}/documents      → ingest; returns DecisionRecord (+ doc/version refs or review ticket); honors Idempotency-Key
GET  /v1/collections/{c}/documents/{id} → current content + version history
GET  /v1/collections/{c}/documents      → list / search
GET  /v1/decisions/{id}                 → full decision record (all signals + rationale)
GET  /v1/collections/{c}/reviews        → pending review queue
POST /v1/reviews/{id}/resolve           → human verdict: as_update | as_new | reject
POST /v1/collections , /v1/sources      → admin: collections, principals, policy, thresholds
GET  /healthz , /metrics
```

### 6.2 MCP tools
`ingest_document`, `get_document`, `get_decision`, `list_documents`, `list_pending_reviews`, `resolve_review` — each delegates to the same core service as the REST routers.

## 7. Data Model (index DB)

- **Collection** — id, name, config (thresholds `LOW`/`HIGH`/`MARGIN`, adjudication on/off, `N`, policy).
- **Principal/Source** — id, auth identity, allowed collections, role (`ingest`/`read`/`reviewer`/`admin`).
- **Document** — id, collection_id, stable_identity (submitter ID/URI or derived), current_version_id, wiki_path, created/updated.
- **DocumentVersion** — id, document_id, content_hash, git storage_ref/commit, submitter, created_at.
- **DecisionRecord** — id, collection_id, input fingerprint, outcome, signals (sim scores, top-2 margin, lexical overlap, adjudicator verdict + rationale), principal, timestamps, resulting_version_id (nullable).
- **ReviewItem** — id, decision_id, status (pending/resolved), candidate matches, resolution, resolver, timestamps.
- **Embedding** — chunk-level vectors (sqlite-vec/pgvector), linked to version + chunk.

## 8. Content Store Layout (per collection git repo)
```
<collection>/
  sources/   # raw submitted documents (kept in git for reproducibility)
  wiki/      # LLM-generated markdown pages
  index.md   # content catalog by category, updated each ingest
  log.md     # append-only chronological record of operations
```

## 9. Error Handling — fail safe, never guess

- Auth fail → `401`; authz fail → `403` (both logged as `REJECTED` with reason).
- Extraction failure (corrupt/unsupported) → `422`, no mutation.
- Provider failure (embed/adjudicate) → retry with backoff; persistent failure routes to `NEEDS_REVIEW` (or `503` if even embedding is impossible). Never a silent store/overwrite.
- **Storage atomicity** — git commit first, then write the version row referencing that commit; a startup reconciler heals partial applies so index and content cannot drift.
- **Idempotency** — same content hash + `Idempotency-Key` returns the original decision, no rework.
- **Concurrency** — per-collection queue serializes; different collections run in parallel.

## 10. Testing Strategy (TDD)

**Unit — decision classifier (crown jewel).** Fixture-driven truth table, provider mocked (deterministic embeddings + canned adjudicator verdicts):

| Input | Expected |
|---|---|
| same ID + same hash | `DUPLICATE` |
| same ID + diff content | `UPDATE` |
| no ID, sim < LOW | `NEW` |
| no ID, sim ≥ HIGH, adjudicator SAME/HIGH | `UPDATE` |
| no ID, gray band | `NEEDS_REVIEW` |
| small top-2 margin | `NEEDS_REVIEW` |
| conflicting signals | `NEEDS_REVIEW` |
| unauthorized | `REJECTED` |

Plus:
- **Auth** — each authenticator; allow/deny matrices.
- **Integration** — full pipeline against a temp git repo + temp SQLite; assert files committed, DB rows, decision records, review queue.
- **Idempotency / concurrency** — duplicate submits, concurrent same-doc submits serialized correctly.
- **Contract** — REST and MCP adapters produce identical decisions for the same input.

## 11. Project Layout
```
llmwiki/
  core/        # decision engine, models, pipeline — no I/O deps (pure, testable)
  auth/        # authenticators + authz policy
  providers/   # LLM/embedding interface + impls (cloud default)
  storage/     # content store (git) + index store (sqlite→pg) + repositories
  api/         # FastAPI app, routers, schemas
  mcp/         # MCP server adapter
  dashboard/   # minimal web UI
  config/      # settings + per-collection config
  cli.py       # serve | init-collection | calibrate | migrate
tests/ {unit, integration, contract}
docker/  docs/  pyproject.toml
```
- Packaging: `pyproject.toml`; runnable as `llmwiki serve`; `docker run` for out-of-the-box use.
- Config via env + per-collection config (file/row).

## 12. Future Phases (out of scope now)
- **Query** operation + filing answers back as pages.
- **Lint** operation (contradiction / stale / orphan / missing cross-reference sweeps).
- Postgres + pgvector backend hardening for HA / multi-writer.
- Optional self-consistency (`N>1`) and richer calibration tooling.
