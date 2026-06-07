# Admin UI: Visual Gate Builder — Design

**Date:** 2026-06-06
**Status:** Approved.
**Scope:** Replace the raw-JSON pipeline textarea in the admin realm page with a client-side visual
builder for composing gates and their rules. Builds on the existing rule framework (SP-G), admin UI
(SP-D), and per-collection config endpoints.

## 1. Summary

Admins currently edit a realm's gate pipeline by hand-writing JSON in a textarea. This adds a
**visual builder**: gates as ordered cards, each holding ordered rule rows; add/remove/reorder gates
and rules; choosing a rule type renders its parameter inputs. On Save the builder serializes to the
pipeline JSON and submits the **existing, already-validated** config endpoint — so no decision-engine
or validation changes. The raw JSON editor is kept as a collapsible "Advanced" fallback.

## 2. Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Interaction model | **Client-side dynamic builder** (vanilla JS, no build toolchain / deps). |
| Param fields | Driven by a **rule catalog** derived from each rule's pydantic `Params` schema. |
| Save path | Serialize → hidden field → POST the **existing** `/admin/realms/{c}/config` (reuse validation + banner). |
| Raw JSON editor | **Keep as a collapsible "Advanced (raw JSON)"** section with its own save. |
| Reorder | **Up/down (▲▼) buttons** for gates and rules (no drag-drop dependency). |
| Create flow | Unchanged — `create_realm` already redirects into the realm page (the builder). |

## 3. Components

### 3.1 Rule catalog (`llmwiki/rules/catalog.py`)
`rule_catalog() -> list[dict]` — for every registered rule, normalize `rule.Params.model_json_schema()`
into:
```json
{"id": "regex_denylist", "category": "validity", "kind": "deterministic",
 "params": [{"name": "patterns", "type": "list", "default": []},
            {"name": "action", "type": "enum", "default": "REVIEW", "options": ["REJECT","REVIEW"]}]}
```
`type ∈ {int, float, str, bool, enum, list}`. Mapping from JSON schema: `integer→int`, `number→float`,
`boolean→bool`, `string→str` (or `enum` when an enum list is present), `array→list`. Defaults come from
the schema; enum options from the enum list. No params → empty list.

### 3.2 API (`llmwiki/api/routers.py`)
`GET /v1/rule-types` → `{"rule_types": rule_catalog()}`. Requires an authenticated principal (any role);
it's non-sensitive metadata.

### 3.3 Admin realm page (`llmwiki/admin_ui.py`)
- Embed the catalog into the page: `<script>const RULE_TYPES = [...]; const PIPELINE = [...];</script>`
  (current effective pipeline = `cfg.pipeline or default_pipeline(cfg)`).
- **Builder DOM + JS** (inline, vanilla):
  - Render each gate as a card: gate-name input, ordered rule rows, and buttons **+ rule**, **remove gate**, **▲ / ▼**.
  - Each rule row: a **type `<select>` grouped by category**; on change, render that type's param inputs
    (number / checkbox / `<select>` for enum / textarea for `rubric` / text for str / comma-separated text
    for list), seeded from current values. Buttons **remove rule**, **▲ / ▼**.
  - Top-level **+ gate** button and a **Save pipeline** button.
  - `serialize()` walks the DOM → `[{gate, rules:[{type, params}]}]`, coercing inputs by type
    (number→int/float, checkbox→bool, comma-split→list, enum/text→str), sets a hidden `pipeline_json`
    input, and submits the existing form to `/admin/realms/{c}/config`.
- **Advanced (collapsible `<details>`):** the existing raw-JSON `<textarea>` + its own "Save raw JSON"
  button posting the same endpoint (escape hatch / copy-paste). Pre-filled with the current pipeline JSON.

## 4. Data flow

```
page load → server embeds RULE_TYPES (catalog) + PIPELINE (current) → JS renders builder
admin edits (add/remove/reorder/params) → Save → JS serialize() → hidden pipeline_json
POST /admin/realms/{c}/config (existing handler) → build_pipeline() validates
  ok → set_collection_config + redirect with "pipeline saved" banner
  invalid → redirect with error banner (422 detail)
```
No change to the decision engine, config schema, or validation.

## 5. Error handling
- Invalid params/types are caught server-side by the existing `build_pipeline()` validation → error banner
  (the builder is best-effort on the client; the server remains the source of truth).
- A pipeline with zero gates is allowed by the engine (everything → `ACCEPT`); the builder permits it but
  the page notes it.
- Catalog endpoint without auth → `401` (same as other endpoints).

## 6. Testing
- **Unit (`tests/unit/test_rule_catalog.py`):** `rule_catalog()` includes all 11 rules; `min_length` →
  `min_chars` int default 40; `regex_denylist` → `action` enum `[REJECT,REVIEW]` + `patterns` list;
  `semantic_replace` → `allow_unsignaled_replace` bool; `exact_duplicate` → empty params.
- **Integration (`tests/integration/`):** `GET /v1/rule-types` returns the catalog (and `401` unauthenticated);
  the realm page embeds `RULE_TYPES`, `PIPELINE`, the builder scaffold, and the Advanced `<details>`; the
  raw-JSON save path still works (regression).
- **Live (browser):** load a realm, add a gate + a rule via the builder, set a param, Save, and confirm the
  saved pipeline via a screenshot + the config endpoint.

## 7. Files
- Create: `llmwiki/rules/catalog.py`, `tests/unit/test_rule_catalog.py`
- Modify: `llmwiki/api/routers.py` (+`GET /v1/rule-types`), `llmwiki/admin_ui.py` (builder),
  `tests/integration/test_admin_ui.py`, `tests/integration/` (catalog endpoint test), `README.md`
- No changes to `rules/engine.py`, `config.py`, or `pipeline.py`.

## 8. Out of scope
Drag-and-drop reordering; a visual builder embedded directly in the create-realm form (create →
redirect-into-builder covers it); editing pipelines outside the admin UI.
