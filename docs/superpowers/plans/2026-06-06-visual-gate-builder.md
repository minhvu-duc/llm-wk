# Visual Gate Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the raw-JSON pipeline textarea in the admin realm page with a client-side visual builder for composing gates and their rules.

**Architecture:** A `rule_catalog()` derives each rule's parameter schema from its pydantic `Params`. It's embedded into the realm page (and exposed at `GET /v1/rule-types`). Vanilla JS renders gates/rules with add/remove/reorder + per-type param inputs, and on Save serializes to the existing, already-validated `/admin/realms/{c}/config` endpoint. The raw JSON editor remains as a collapsible "Advanced" fallback.

**Tech Stack:** Python/FastAPI, pydantic v2 (`model_json_schema`), inline vanilla JS (no build toolchain). Reuses the rule registry, config endpoint, and validation.

**Spec:** `docs/superpowers/specs/2026-06-06-visual-gate-builder-design.md`

---

### Task 1: `rule_catalog()`

**Files:** Create `llmwiki/rules/catalog.py`; Test `tests/unit/test_rule_catalog.py`

- [ ] **Step 1: failing test**

```python
# tests/unit/test_rule_catalog.py
from llmwiki.rules.catalog import rule_catalog


def _by_id(cat):
    return {r["id"]: r for r in cat}


def test_catalog_covers_all_rules_with_shape():
    cat = rule_catalog()
    ids = _by_id(cat)
    for rid in ["min_length", "content_type", "regex_denylist", "knowledge_worthiness",
                "exact_duplicate", "identity_match", "semantic_duplicate",
                "version_on_change", "semantic_replace", "confidence_route", "accept"]:
        assert rid in ids
    assert ids["min_length"]["category"] == "validity"
    assert ids["semantic_duplicate"]["kind"] == "semantic"


def test_param_types_and_defaults():
    ids = _by_id(rule_catalog())
    ml = {p["name"]: p for p in ids["min_length"]["params"]}
    assert ml["min_chars"]["type"] == "int" and ml["min_chars"]["default"] == 40

    dl = {p["name"]: p for p in ids["regex_denylist"]["params"]}
    assert dl["patterns"]["type"] == "list"
    assert dl["action"]["type"] == "enum" and set(dl["action"]["options"]) == {"REJECT", "REVIEW"}

    sr = {p["name"]: p for p in ids["semantic_replace"]["params"]}
    assert sr["allow_unsignaled_replace"]["type"] == "bool"
    assert sr["threshold"]["type"] == "float"

    assert ids["exact_duplicate"]["params"] == []
```

- [ ] **Step 2: run, expect fail** — `.venv/bin/pytest tests/unit/test_rule_catalog.py -q` → `ModuleNotFoundError`

- [ ] **Step 3: implement `llmwiki/rules/catalog.py`**

```python
from __future__ import annotations
from llmwiki.rules.base import known_rules, get_rule

_CAT_ORDER = ["validity", "existence", "update_replace", "routing"]


def _field(name: str, prop: dict) -> dict:
    if "enum" in prop:
        return {"name": name, "type": "enum",
                "default": prop.get("default", prop["enum"][0]), "options": prop["enum"]}
    if "const" in prop:
        return {"name": name, "type": "enum",
                "default": prop.get("default", prop["const"]), "options": [prop["const"]]}
    t = prop.get("type")
    if t == "integer":
        return {"name": name, "type": "int", "default": prop.get("default", 0)}
    if t == "number":
        return {"name": name, "type": "float", "default": prop.get("default", 0.0)}
    if t == "boolean":
        return {"name": name, "type": "bool", "default": prop.get("default", False)}
    if t == "array":
        return {"name": name, "type": "list", "default": prop.get("default", [])}
    return {"name": name, "type": "str", "default": prop.get("default", "")}


def rule_catalog() -> list[dict]:
    rules = [get_rule(r) for r in known_rules()]
    rules.sort(key=lambda r: (_CAT_ORDER.index(r.category) if r.category in _CAT_ORDER else 99, r.id))
    out = []
    for r in rules:
        props = r.Params.model_json_schema().get("properties", {})
        out.append({"id": r.id, "category": r.category, "kind": r.kind,
                    "params": [_field(n, p) for n, p in props.items()]})
    return out
```

- [ ] **Step 4: run, expect pass.** If a `Literal` renders via `$ref`/`allOf` instead of inline `enum`, resolve it: read `r.Params.model_json_schema()` output and extend `_field` to follow `$ref` into `$defs` (only if the test fails).

- [ ] **Step 5: commit** — `git add llmwiki/rules/catalog.py tests/unit/test_rule_catalog.py && git commit -m "feat: rule_catalog() exposing rule param schemas"`

---

### Task 2: `GET /v1/rule-types`

**Files:** Modify `llmwiki/api/routers.py`; Test `tests/integration/test_rule_types_api.py`

- [ ] **Step 1: failing test**

```python
# tests/integration/test_rule_types_api.py
from fastapi.testclient import TestClient
from llmwiki.api.app import create_app
from llmwiki.auth.base import Principal
from llmwiki.auth.apikey import ApiKeyAuthenticator


def client(tmp_path):
    auth = ApiKeyAuthenticator({"k": Principal(id="k", allowed_collections=["*"], roles=["read"])})
    return TestClient(create_app(data_dir=str(tmp_path), authenticator=auth, provider_name="fake"))


def test_rule_types_lists_catalog(tmp_path):
    r = client(tmp_path).get("/v1/rule-types", headers={"authorization": "Bearer k"})
    assert r.status_code == 200
    ids = {rt["id"] for rt in r.json()["rule_types"]}
    assert {"min_length", "semantic_duplicate", "semantic_replace"} <= ids


def test_rule_types_requires_auth(tmp_path):
    assert client(tmp_path).get("/v1/rule-types").status_code == 401
```

- [ ] **Step 2: run, expect fail** (404/401 mismatch — route missing)

- [ ] **Step 3: implement** — in `llmwiki/api/routers.py` add the import `from llmwiki.rules.catalog import rule_catalog`, and inside `build_router()`:

```python
    @r.get("/rule-types")
    def rule_types(request: Request):
        _principal(request)  # any authenticated principal; non-sensitive metadata
        return {"rule_types": rule_catalog()}
```

- [ ] **Step 4: run, expect pass**

- [ ] **Step 5: commit** — `git add llmwiki/api/routers.py tests/integration/test_rule_types_api.py && git commit -m "feat: GET /v1/rule-types catalog endpoint"`

---

### Task 3: Visual builder on the realm page

**Files:** Modify `llmwiki/admin_ui.py`; Test `tests/integration/test_admin_ui.py` (extend)

Replace the **"Edit pipeline (JSON)"** card (currently a single textarea form, ~lines 169-172 in the realm body) with: (a) a **Pipeline builder** card containing `#builder`, an `+ gate` button, a form with hidden `pipeline_json` + Save, an embedded `<script>` with `RULE_TYPES`/`PIPELINE` + the builder JS; and (b) an **Advanced (raw JSON)** collapsible `<details>` holding the old textarea form. Keep the read-only "Pipeline" summary card and the save endpoint unchanged.

- [ ] **Step 1: failing test (extend `tests/integration/test_admin_ui.py`)**

```python
def test_realm_page_has_visual_builder_and_catalog(tmp_path):
    c = logged_in(tmp_path)
    page = c.get("/admin/realms/kb").text
    assert 'id="builder"' in page                    # builder mount point
    assert "const RULE_TYPES" in page                # embedded catalog
    assert "const PIPELINE" in page                  # current pipeline seed
    assert "min_length" in page and "semantic_replace" in page
    assert "Advanced (raw JSON)" in page             # collapsible fallback
    assert 'name="pipeline_json"' in page            # still posts the existing field


def test_raw_json_save_still_works(tmp_path):
    c = logged_in(tmp_path)
    import json as _j
    good = _j.dumps([{"gate": "dedup", "rules": [{"type": "exact_duplicate"}]}])
    r = c.post("/admin/realms/kb/config", data={"pipeline_json": good})
    assert r.status_code == 200 and "pipeline saved" in r.text
```

- [ ] **Step 2: run, expect fail** — `.venv/bin/pytest tests/integration/test_admin_ui.py -q -k "visual_builder or raw_json_save"`

- [ ] **Step 3: implement the builder in `llmwiki/admin_ui.py`.**

In the `realm()` route, build a JSON-encoded catalog + pipeline for embedding:
```python
        from llmwiki.rules.catalog import rule_catalog
        rule_types_js = json.dumps(rule_catalog())
        pipeline_js = json.dumps(pipeline)
```
Replace the old "Edit pipeline (JSON)" card in `body` with the builder + advanced sections:
```python
            f'<div class="card"><h3>Pipeline builder</h3>'
            f'<div id="builder"></div>'
            f'<button type="button" onclick="addGate()">+ gate</button>'
            f'<form id="pform" method="post" action="/admin/realms/{collection}/config" '
            f'onsubmit="return doSave()"><input type="hidden" name="pipeline_json" id="pjson">'
            f'<button type="submit">Save pipeline</button></form></div>'
            f'<details class="card"><summary>Advanced (raw JSON)</summary>'
            f'<form method="post" action="/admin/realms/{collection}/config">'
            f'<textarea name="pipeline_json">{editor_json}</textarea><br>'
            f'<button type="submit">Save raw JSON</button></form></details>'
            f'<script>const RULE_TYPES={rule_types_js};const PIPELINE={pipeline_js};{_BUILDER_JS}</script>'
```
Add a module-level `_BUILDER_JS` string with the vanilla builder (state from `PIPELINE`, render gates/rules, add/remove/▲▼, per-type param inputs — number/checkbox/select/textarea-for-rubric/comma-list, `doSave()` serializes `state` into `#pjson` and submits). Escape helper handles `& < > "`. (Full JS authored in this step; verified live in the browser in Task 5.)

- [ ] **Step 4: run, expect pass** — `.venv/bin/pytest tests/integration/test_admin_ui.py -q`

- [ ] **Step 5: commit** — `git add llmwiki/admin_ui.py tests/integration/test_admin_ui.py && git commit -m "feat: visual gate builder on the realm page"`

---

### Task 4: Full suite + README

**Files:** Modify `README.md`; run suite

- [ ] **Step 1:** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 2:** In `README.md` "Composable gates & rules" section, add a line: the admin UI provides a **visual builder** (`/admin/realms/{c}`) to add/reorder gates and rules without writing JSON; the raw JSON remains under "Advanced", and the catalog is at `GET /v1/rule-types`.
- [ ] **Step 3:** commit — `git add README.md && git commit -m "docs: document the visual gate builder"`

---

### Task 5: Live browser verification

- [ ] **Step 1:** Start a server on a free port (avoid Docker's 8001), fresh data dir.
- [ ] **Step 2:** In a browser: log in, create/open a realm, **add a gate**, **add a rule** (pick a type, set a param), **Save**.
- [ ] **Step 3:** Confirm via screenshot + `GET /v1/collections/{c}/config` that the saved pipeline matches what was built. Reorder a gate, save, re-confirm. Stop the server.

---

## Self-Review (plan vs spec)
- Catalog with normalized types/defaults/enums → Task 1. ✅
- `GET /v1/rule-types` (auth required) → Task 2. ✅
- Builder embeds catalog + pipeline, add/remove/reorder, per-type params, serializes to existing config endpoint → Task 3. ✅
- Advanced raw-JSON fallback kept + still works → Task 3 (test). ✅
- No engine/config/pipeline changes → confirmed (only catalog + routers + admin_ui). ✅
- Live JS interaction verified → Task 5. ✅
- Types consistent: catalog field `type ∈ {int,float,str,bool,enum,list}` used identically in `_field` and the builder JS; `pipeline_json` field name reused from the existing endpoint. ✅
