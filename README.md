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

## Quality-control gates

Before dedup, each document passes a configurable chain of gates (default order
`min_info → denylist → knowledge`). Gates run **before embedding**, so filtered content costs
nothing. A gate returns PASS / REJECT / REVIEW; REJECT → `REJECTED` (logged with the gate +
reason), REVIEW → `NEEDS_REVIEW` (human queue), PASS → dedup/version.

The `knowledge` gate asks the LLM whether the text is worth storing, using a per-collection
**rubric** you control. Set rules per collection (admin only):

```bash
curl -X PUT localhost:8000/v1/collections/kb/config -H "authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d '{"min_chars":40,"knowledge_rubric":"Keep durable facts and customer preferences; drop greetings and small talk.","denylist_patterns":["\\bSSN\\b"],"denylist_action":"REVIEW"}'
```

`GET /v1/collections/{c}/config` reads the current rules. Disable filtering with
`{"quality_enabled": false}`.

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

Review queue dashboard: `http://localhost:8000/dashboard/kb`.

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

- **REST** — `POST /v1/collections/{c}/documents`, `GET /v1/decisions/{id}`,
  `GET /v1/collections/{c}/reviews`, `POST /v1/reviews/{id}/resolve`, `GET /healthz`.
- **MCP** — `ingest_document`, `get_decision`, `list_pending_reviews` (see
  `llmwiki/mcp_server.py::serve_mcp`). REST and MCP share one core, verified by a contract test.

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
