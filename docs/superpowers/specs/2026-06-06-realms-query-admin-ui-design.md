# LLM-Wiki Platform: Realms (SP-B), Query (SP-C), Admin UI (SP-D) — Design

**Date:** 2026-06-06
**Status:** Approved by delegation (user: "implement B, C and D, you can make decisions, no approval needed").
**Scope:** Three sequenced sub-projects built on the existing engine + composable rule framework (SP-G).
SP-A (pluggable BYO storage) remains a separate later cycle; these build on the current SQLite+git store.

## 0. Terminology

A **realm / zone** is a **collection** — the existing per-collection isolation unit (its own git repo,
its own config/pipeline, its own documents). We keep `collection` in code and API paths for continuity;
the admin UI labels them "Realms (zones)". No mass rename.

---

## SP-B — Realms & API-key scoping

### Problem
API keys are hard-coded in the CLI dev authenticator. There is no way for an admin to mint keys scoped
to specific zones for push/query. Authz already supports per-collection + roles; we need **persistence +
management + a query permission + a global wildcard**.

### Decisions
- **Persistent API keys** in the index DB. Each key *is* a principal. Table `api_keys`:
  `id, key_hash (sha256), name, allowed_collections (JSON), roles (JSON), revoked (int), created_at`.
- **Key format** `lw_<32 hex>`; only the SHA-256 hash is stored. The raw secret is returned **once** at creation.
- **`StoredAuthenticator(index)`** — hashes the bearer token, looks up a non-revoked key, returns a
  `Principal(id, allowed_collections, roles)`; raises `AuthError` otherwise.
- **Roles vocabulary:** `ingest`, `read`, `query`, `reviewer`, `admin`. (`query` is new — search permission,
  distinct from `read` = fetch a specific doc/decision.)
- **Wildcard:** `allowed_collections == ["*"]` means all zones. `authorize()` is updated to honor `*`.
- **Management endpoints (admin-only):**
  - `POST /v1/keys` `{name, allowed_collections, roles}` → `{id, name, key}` (raw key shown once)
  - `GET /v1/keys` → list `{id, name, allowed_collections, roles, revoked}` (no secrets)
  - `DELETE /v1/keys/{id}` → revoke (sets `revoked=1`)
- **Bootstrap:** `llmwiki serve` seeds an admin key from `LLMWIKI_API_KEY` into the DB if absent, then uses
  `StoredAuthenticator`. Existing `ApiKeyAuthenticator` stays for tests/embedding.

### Files
- Modify `llmwiki/storage/index_store.py` — `api_keys` table + `create_api_key`, `get_api_key_by_hash`,
  `list_api_keys`, `revoke_api_key`, `list_collections`.
- Create `llmwiki/auth/stored.py` — `StoredAuthenticator`, `hash_key`, `generate_key`.
- Modify `llmwiki/auth/authz.py` — honor `*` wildcard.
- Modify `llmwiki/api/{schemas,routers}.py` — key endpoints.
- Modify `llmwiki/cli.py` — seed admin key + use `StoredAuthenticator`.

---

## SP-C — Query API

### Problem
There is no read/query side. A service should query a zone or globally, permissioned by its key.

### Decisions
- **Similarity search** over current **active** versions, reusing embeddings + cosine (brute-force, like dedup;
  `sqlite-vec`/`pgvector` is the SP-A scale path).
- `IndexStore.search(collection, embedding, top_k)` → `[{document_id, score, content, wiki_path}]`.
- **Endpoints:**
  - `POST /v1/collections/{c}/query` `{query, top_k=5}` — search one zone. Requires `query` on `c`.
  - `POST /v1/query` `{query, top_k=5, collections?}` — global: search across the principal's allowed zones
    (resolve `*` → all zones; if `collections` given, intersect with allowed). Requires `query`. Results
    merged, ranked, annotated with their `collection`.
- **MCP:** add a `query` tool (single-zone) sharing the same core.
- Embedding the query uses the same provider as ingest.

### Files
- Modify `llmwiki/storage/index_store.py` — `search`.
- Create `llmwiki/query.py` — `QueryService(index, provider)` with `query_zone` / `query_global`.
- Modify `llmwiki/api/{schemas,routers}.py` — query endpoints.
- Modify `llmwiki/mcp_server.py` — `query` tool.

### Result shape
`{ "results": [ {document_id, collection, score, snippet, wiki_path} ], "query": "..." }`
(`snippet` = first ~300 chars of content.)

---

## SP-D — Admin UI

### Problem
Only a minimal read-only dashboard exists. Admins need an intuitive UI to configure pipelines, manage
keys/realms, work the review queue, and learn the gate concepts.

### Decisions
- **Server-rendered HTML** (FastAPI `HTMLResponse`, inline templates — no build toolchain, no template-engine
  dependency), plus tiny vanilla interactions via plain forms. Mounted under `/admin`.
- **Cookie auth:** `GET /admin/login` (form) → `POST /admin/login` verifies the submitted key authenticates
  with the `admin` role, sets an `httponly` cookie `lw_key`, redirects to `/admin`. All `/admin/*` pages read
  the cookie and require `admin`; otherwise redirect to login.
- **Pages:**
  - `/admin` — realms (collections) list + link to key management + help.
  - `/admin/realms/{c}` — **pipeline visualizer** (gates → rule cards), a JSON pipeline **editor** (textarea
    → `PUT config`, with validation feedback), the **review queue** with Approve-as-update / Approve-as-new /
    Reject buttons (POST forms), and recent decisions.
  - `/admin/keys` — list keys + "create key" form (shows the raw key once after creation).
  - `/admin/help` — explains gates/rules/categories/dispositions (the "teach the concepts" requirement).
- **Review actions** reuse the existing review-resolve logic.
- The legacy `/dashboard/{c}` stays for back-compat.

### Files
- Create `llmwiki/admin_ui.py` — `attach_admin(app)` with all `/admin/*` routes + inline templates.
- Modify `llmwiki/cli.py` — attach the admin UI in `serve`.

---

## Cross-cutting

- **Error handling:** auth failures → redirect to login (UI) or `401/403` (API); invalid pipeline JSON in the
  editor → re-render with the `422` detail; unknown key id on revoke → `404`.
- **Security:** only SHA-256 hashes stored; raw keys shown once; admin-gated management + UI; `httponly` cookie.
- **Testing:** key store + `StoredAuthenticator` + wildcard authz (unit); key endpoints + query endpoints
  (zone, global, permission denials) + MCP query contract (integration); admin UI login gate, pages render,
  pipeline save, review-resolve flow, key creation (integration via `TestClient`).
- **Docs:** README gains "Realms & API keys", "Query", and "Admin UI" sections.

## Out of scope
SP-A pluggable storage; real-time collaborative editing; RBAC beyond the role list; SSO/OIDC UI login
(API supports OIDC tokens already via the authenticator interface, but the UI uses key-cookie login in v1).
