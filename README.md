# llmwiki

Open-source **LLM-Wiki ingest decision engine** for enterprise. Submit a document; the
engine decides `NEW` / `UPDATE` / `DUPLICATE` / `NEEDS_REVIEW` / `REJECTED` and persists it
to a git-versioned markdown wiki + SQLite index. One shared core, exposed over **REST** and
**MCP**, with a minimal review dashboard.

Inspired by Andrej Karpathy's [LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f):
the LLM maintains a persistent, compounding knowledge base instead of re-deriving everything
per query. This project implements the **ingest decision** — the hard part of *deciding*
whether and how a document enters the store.

## How the decision works

1. **Trust gate** — authenticate the submitter (API key / OIDC / mTLS) → resolve a principal
   → check it's authorized for the target collection. A document is "authentic" because it
   came from a trusted, authorized source.
2. **Identity** — match on a submitter-provided stable ID/URI when present; otherwise infer
   from content hash + semantic similarity.
3. **Classify** — similarity bands + top-2 margin + (in the gray band) a structured LLM
   adjudicator verdict + a conflicting-signal check. On uncertainty it **never auto-updates** —
   it routes to human review.

| Outcome | When |
|---|---|
| `REJECTED` | failed the trust gate, or a quality gate dropped it as non-knowledge |
| `DUPLICATE` | same identity + same content hash |
| `UPDATE` | same identity, content changed |
| `NEW` | no match |
| `NEEDS_REVIEW` | ambiguous (dedup) or borderline quality — queued for a human |

## Composable gates & rules

A collection's decision pipeline is a configurable list of **gates**, each containing **rules**
from a categorized palette. Rules are evaluated in order, first-match within a gate, and the first
gate to fire a terminal disposition short-circuits the chain (otherwise the doc is `ACCEPT`ed).
Validity/semantic rules run before embedding, so filtered content costs nothing.

Categories & built-in rule types:
- **Validity** — `min_length`, `content_type`, `regex_denylist`, `knowledge_worthiness`
- **Existence** — `exact_duplicate`, `identity_match`, `semantic_duplicate`
- **Update/Replace** — `version_on_change`, `semantic_replace`
- **Routing** — `confidence_route`, `accept`

Set a collection's pipeline (admin only):

```bash
curl -X PUT localhost:8000/v1/collections/kb/config -H "authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d '{"pipeline":[
        {"gate":"validity","rules":[{"type":"min_length","params":{"min_chars":40}},
                                    {"type":"knowledge_worthiness","params":{"rubric":"Keep durable facts; drop small talk.","on_uncertain":"REVIEW"}}]},
        {"gate":"dedup","rules":[{"type":"exact_duplicate"},{"type":"identity_match"},{"type":"semantic_duplicate"}]},
        {"gate":"update","rules":[{"type":"version_on_change"}]}]}'
```

Or build it visually: the admin UI at `/admin/realms/{c}` has a **visual gate builder** — add/reorder
gates and rules and fill each rule's parameters with form inputs (no JSON), with the raw JSON kept under
"Advanced". The rule palette is also available programmatically at `GET /v1/rule-types`.

With no pipeline configured, a default pipeline reproduces the built-in behavior.
`GET /v1/collections/{c}/config` reads the current rules. `semantic_replace` (supersede a
different existing doc) is opt-in and review-by-default — it only auto-replaces with a direction
signal (a `supersedes` metadata hint) or when `allow_unsignaled_replace` is set.

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# offline, deterministic default provider ("fake") — no API key needed
LLMWIKI_API_KEY=dev-key .venv/bin/llmwiki serve &

# the dev key is an admin principal; create a collection and ingest
curl -s localhost:8000/v1/collections -H "authorization: Bearer dev-key" \
     -H 'content-type: application/json' -d '{"name":"kb"}'

curl -s localhost:8000/v1/collections/kb/documents -H "authorization: Bearer dev-key" \
     -H 'content-type: application/json' \
     -d '{"content":"The enterprise refund window is thirty days from invoice.","declared_id":"d1"}'
# -> {"outcome":"NEW", ...}; submitting the same body again -> {"outcome":"DUPLICATE"}
# (trivial content like "hi thanks" is filtered by the quality gate -> {"outcome":"REJECTED"})
```

Admin UI: `http://localhost:8000/admin` (log in with the admin key). Minimal review dashboard
also at `http://localhost:8000/dashboard/kb`.

## Realms, API keys & access

A **realm (zone)** is a collection — an isolated unit with its own pipeline, documents, and git
history. Access is by **API key**, scoped to zones + roles. `llmwiki serve` seeds an admin key from
`LLMWIKI_API_KEY`; mint scoped keys with it:

```bash
curl -s localhost:8000/v1/keys -H "authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d '{"name":"chat-agent","allowed_collections":["kb"],"roles":["ingest","query"]}'
# -> {"id":"key_...","key":"lw_...", ...}   (the raw key is shown once)
```

Roles: `ingest` (push), `read` (fetch a doc), `query` (search), `reviewer`, `admin`.
`allowed_collections:["*"]` grants all zones. Keys are stored as SHA-256 hashes only; list with
`GET /v1/keys`, revoke with `DELETE /v1/keys/{id}` (admin only).

## Querying

```bash
# search one zone (needs `query` on that zone)
curl -s localhost:8000/v1/collections/kb/query -H "authorization: Bearer $KEY" \
  -H 'content-type: application/json' -d '{"query":"refund window","top_k":5}'

# search across all zones the key may read
curl -s localhost:8000/v1/query -H "authorization: Bearer $KEY" \
  -H 'content-type: application/json' -d '{"query":"refund window"}'
# -> {"query":"...","zones":[...],"results":[{document_id,collection,score,snippet,wiki_path}]}
```

Global query spans the principal's allowed zones (`*` → all); pass `collections` to narrow.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `LLMWIKI_DATA` | `./data` | data dir (SQLite index + per-collection git repos) |
| `LLMWIKI_PROVIDER` | `fake` | `fake` (offline) or `litellm` (cloud) |
| `LLMWIKI_API_KEY` | `dev-key` | dev admin API key for `llmwiki serve` |
| `LLMWIKI_EMBED_MODEL` | `text-embedding-3-small` | embedding model (litellm) |
| `LLMWIKI_ADJ_MODEL` | `gpt-4o-mini` | adjudication model (litellm) |

Set `LLMWIKI_PROVIDER=litellm` plus the relevant provider API key env var (e.g.
`OPENAI_API_KEY`) for cloud embeddings/adjudication. The provider is pluggable — swap in a
self-hosted backend by implementing `llmwiki.providers.base.Provider`.

## Interfaces

- **REST** — ingest `POST /v1/collections/{c}/documents`; query `POST /v1/collections/{c}/query`,
  `POST /v1/query`; config `GET|PUT /v1/collections/{c}/config`; keys `POST|GET /v1/keys`,
  `DELETE /v1/keys/{id}`; reviews `GET /v1/collections/{c}/reviews`, `POST /v1/reviews/{id}/resolve`;
  `GET /v1/decisions/{id}`; `GET /healthz`.
- **MCP** — `ingest_document`, `query`, `get_decision`, `list_pending_reviews` (see
  `llmwiki/mcp_server.py::serve_mcp`). REST and MCP share one core, verified by a contract test.
- **Admin UI** — `/admin` (key-cookie login): realms, visual pipeline + JSON editor, review queue
  actions, API-key management, and a gate-concepts help page.

## Storage

- **Content** — one git repo per collection: `sources/` (raw), `wiki/` (generated markdown),
  `index.md`, `log.md`. Git gives versioning, diff, and an audit trail for free.
- **Index** — SQLite (documents, versions, embeddings, decisions, reviews). Postgres + pgvector
  is the documented scale path.

## Development

```bash
.venv/bin/pytest -q        # full suite
```

Design spec: `docs/superpowers/specs/2026-06-04-llm-wiki-decision-engine-design.md`
Implementation plan: `docs/superpowers/plans/2026-06-04-llm-wiki-decision-engine.md`

## Scope

v1 is the ingest decision engine. Query/search-answering and the periodic lint
(contradiction/stale/orphan sweeps) from the LLM-Wiki pattern are future phases.
