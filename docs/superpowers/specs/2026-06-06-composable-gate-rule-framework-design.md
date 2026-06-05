# LLM-Wiki: Composable Gate & Rule Framework (SP-G) — Design

**Date:** 2026-06-06
**Status:** Approved (brainstorming complete)
**Scope:** SP-G — refactor the hard-coded gate/classifier logic into a **data-driven, admin-composable
gate + rule framework**. This is the first sub-project of the larger multi-tenant platform vision;
**pluggable storage (SP-A), realms/API-key scoping (SP-B), query (SP-C), and admin UI (SP-D) are
separate later cycles** and are out of scope here.

## 1. Summary

Today the engine runs a fixed chain (trust gate → quality gate chain → dedup/version classifier).
The vision is **"gates are Lego blocks"**: an admin assembles as many gates as they want, fills each
with **rules** chosen from a **categorized palette**, and the engine evaluates them to decide what
happens to an incoming document.

This sub-project generalizes the existing logic into that framework — the current gates and classifier
become **rule primitives** — without changing the external decision outcomes or the
`DecisionRecord`/review-queue machinery. A **default pipeline reproduces today's behavior** so existing
collections keep working.

## 2. Goals & Non-Goals

### Goals
- A composable model: **gates contain rules; rules are categorized**; admins add/order as many gates
  as they want.
- A registry of **rule primitives** (deterministic *and* semantic) admins configure by parameters (no code).
- An **ordered, first-match, short-circuit** evaluator with a **shared context** carrying findings between gates.
- Refactor existing gates + classifier into primitives; preserve outcomes; ship a default pipeline for
  backward compatibility.
- Add `REPLACE` (supersede a different existing doc) with conservative, review-by-default semantics.

### Non-Goals
- Pluggable storage / BYO DB (SP-A), realms & API-key scoping (SP-B), query (SP-C), admin UI (SP-D).
- Admin-authored rule logic via DSL or code (palette is parameterized; new primitive *types* are
  developer-registered).
- Fact-merging into topic pages (separate future cycle).

## 3. Core Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Gate/rule/category relationship | **Gate contains rules; rules are categorized.** |
| Rule categories | **Validity, Existence/Duplication, Update vs Replace, Routing/Disposition.** |
| Evaluation | **Ordered first-match, short-circuit, with a shared context.** |
| Rule power | **Parameterized palette (no-code); new primitive types via developer registry.** |
| Non-binary rules | **Semantic primitives** resolve a score/verdict via thresholds + a confidence band; uncertainty → `REVIEW`. |
| `semantic_replace` | **Hybrid retrieve + LLM `SUPERSEDES` verdict + a direction signal; review-by-default; auto-replace opt-in per realm.** |

## 4. Domain Model

- **RuleType** (developer-registered primitive): `id`, `category`, `kind` (`deterministic`|`semantic`),
  a pydantic `params` schema, and `evaluate(doc, ctx, params) -> RuleResult`.
- **Rule** (config instance): a `type` (RuleType id) + filled `params`.
- **Gate** (config): `name` + ordered `rules`.
- **Pipeline** (config): ordered `gates`.
- **Disposition**: `PASS` (non-terminal) or terminal `REJECT | REVIEW | DUPLICATE | UPDATE | REPLACE | ACCEPT`.
- **RuleResult**: `{ disposition, signals: dict, context_updates: dict }`.
- **EvalContext**: `doc`, `provider`, `storage` handle, and **findings** (e.g. matched candidate + score),
  accumulated across rules/gates.

**Category → allowed dispositions** (category constrains a rule's effect; each rule instance picks its
specific disposition within that set):
- **Validity** → `REJECT` | `REVIEW`
- **Existence** → `DUPLICATE` (terminal) | annotate match + `PASS`
- **Update vs Replace** → `UPDATE` | `REPLACE`
- **Routing** → `ACCEPT` | `REVIEW` | `REJECT`

## 5. Evaluation Engine

```
build pipeline from realm config
ctx = EvalContext(doc, provider, storage, findings={})
for gate in pipeline.gates:                 # ordered
    for rule in gate.rules:                 # ordered
        result = ruletype.evaluate(doc, ctx, rule.params)
        ctx.apply(result.context_updates)   # annotate findings (e.g. matched doc)
        if result.disposition != PASS:       # first terminal in the gate wins
            gate_disposition = result.disposition
            break
    if gate fired a terminal disposition:
        return decision(gate_disposition, ctx)   # short-circuit whole chain
return decision(ACCEPT, ctx)                 # no gate fired -> store as new
```

- **First-match within a gate; first-firing gate short-circuits the chain.**
- A rule may **annotate context and `PASS`** (e.g. Existence records a fuzzy match for a later
  Update/Replace rule), or **fire a terminal disposition**.
- **Cheap deterministic rules run before semantic ones** (cost ordering); semantic rules only run when reached.

## 6. Rule Palette (v1 built-ins)

These *are* the refactor of existing logic.

| Category | Primitive | kind | Params | Effect |
|---|---|---|---|---|
| Validity | `min_length` | det | `min_chars` | `REJECT` |
| Validity | `content_type` | det | `allowed[]` | `REJECT` |
| Validity | `regex_denylist` | det | `patterns[]`, `action` | `REJECT`/`REVIEW` |
| Validity | `knowledge_worthiness` | sem | `rubric`, `on_uncertain` | `REJECT`/`REVIEW`/`PASS` |
| Existence | `exact_duplicate` | det | — | `DUPLICATE` |
| Existence | `identity_match` | det | — | `DUPLICATE`, or annotate + `PASS` |
| Existence | `semantic_duplicate` | sem | `threshold_high`, `gray_band`, `on_uncertain` | `DUPLICATE`, or annotate + `PASS` |
| Update/Replace | `version_on_change` | det | — | `UPDATE` |
| Update/Replace | `semantic_replace` | sem | `threshold`, `on_uncertain`, `allow_unsignaled_replace` | `REPLACE`/`REVIEW`/`PASS` |
| Routing | `confidence_route` | det | `min_confidence`, `on_low` | `REVIEW` |
| Routing | `accept` | det | — | `ACCEPT` |

## 7. `semantic_replace` (detailed)

The highest-stakes rule (it archives existing knowledge), so it is conservative by design.

1. **Retrieve** the candidate(s) from `ctx.findings` (the Existence gate already retrieved them — no
   second search). If none, `PASS`.
2. **Judge** supersession with the provider: extend the adjudicator relationship vocabulary with
   `SUPERSEDES`; it returns `relationship` + `confidence` + `rationale`.
3. **Direction signal** — any of: (a) explicit `supersedes: <doc_id>` hint in the incoming metadata,
   (b) recency (`timestamp`/`effective_date` newer than the candidate), (c) source-authority ranking.
4. **Decision:**
   - `SUPERSEDES` + high confidence + a direction signal → `REPLACE`.
   - `SUPERSEDES` + high confidence but **no** direction signal → `REPLACE` only if
     `allow_unsignaled_replace` (per-realm, default **off**), else `REVIEW`.
   - gray band / low confidence / `CONFLICTING` → `REVIEW`.
   - otherwise → `PASS`.
5. **On `REPLACE`:** the matched existing doc is **archived + linked** (`replaced_by` on the old,
   `replaces` on the new); the new doc becomes current. (Hard delete is never performed.)

Models are pluggable via the existing `Provider` (embeddings for retrieval, `adjudicate` for the
verdict); a rule param may select a specific model profile.

## 8. Configuration

The flat quality fields (`gate_order`, `min_chars`, `denylist_patterns`, …) are replaced by a structured
`pipeline` on `CollectionConfig`:

```json
{"pipeline":[
  {"gate":"validity","rules":[
     {"type":"min_length","params":{"min_chars":40}},
     {"type":"regex_denylist","params":{"patterns":["\\bSSN\\b"],"action":"REVIEW"}},
     {"type":"knowledge_worthiness","params":{"rubric":"...","on_uncertain":"REVIEW"}}]},
  {"gate":"dedup","rules":[
     {"type":"exact_duplicate"},
     {"type":"identity_match"},
     {"type":"semantic_duplicate","params":{"threshold_high":0.95,"gray_band":0.85,"on_uncertain":"REVIEW"}}]},
  {"gate":"update","rules":[
     {"type":"version_on_change"},
     {"type":"semantic_replace","params":{"threshold":0.9,"on_uncertain":"REVIEW","allow_unsignaled_replace":false}}]}
]}
```

- A **default pipeline** (constructed when no `pipeline` is configured) reproduces today's behavior.
- Each rule's `params` is validated against its RuleType schema at config-set time; **unknown type,
  invalid params, or bad regex → `422`** (admin-gated `PUT /collections/{c}/config`, unchanged endpoint).

## 9. Structure (files)

**Create:** `llmwiki/rules/__init__.py`, `rules/base.py` (RuleType, Rule, Gate, Pipeline, RuleResult,
Disposition, EvalContext, registry), `rules/palette.py` (built-in primitives), `rules/engine.py`
(build-from-config + evaluator).

**Modify:** `llmwiki/pipeline.py` (call the rule engine instead of `build_chain` + `classify`);
`llmwiki/config.py` (`pipeline` field + default-pipeline factory); `llmwiki/providers/base.py` +
`litellm`/`fake` (`SUPERSEDES` in the adjudicator vocabulary; a direction-aware replace verdict);
`llmwiki/api/routers.py` (validate `pipeline` config).

**Supersede:** the `llmwiki/gates/` package and `classifier.py` logic are re-homed into `rules/palette.py`
(kept as thin shims if needed for transition, removed once the engine is the single path).

## 10. Error Handling

- Unknown rule type / invalid params / bad regex → `422` at config-set.
- Semantic-rule provider failure → the rule's `on_uncertain` disposition (default `REVIEW`); the engine
  **never silently `ACCEPT`s** on error.
- `REPLACE` never hard-deletes; archive + link is the only effect.
- Idempotency, the `DecisionRecord` log, and the review queue are unchanged.

## 11. Testing

- **Per-primitive units:** each deterministic primitive; each semantic primitive with a fake provider
  (threshold/confidence bands, `on_uncertain` routing).
- **Engine:** first-match within a gate; first-firing gate short-circuits; the
  Existence-annotates → Update-reads **context flow**; default `ACCEPT` when nothing fires.
- **`semantic_replace`:** SUPERSEDES + signal → `REPLACE`; SUPERSEDES + no signal + flag off → `REVIEW`;
  flag on → `REPLACE`; gray/low/conflicting → `REVIEW`; old doc archived + linked, never deleted.
- **Config:** parse/validate `pipeline`; unknown type → `422`; default pipeline builds correctly.
- **Parity (regression safety):** the default pipeline reproduces the existing test corpus's decisions,
  proving the refactor preserved behavior.

## 12. Future (other platform sub-projects)
SP-A pluggable storage, SP-B realms & API-key scoping (push + query), SP-C query API (zone/global),
SP-D admin UI (compose/visualize gates, manage keys/realms, review queue). Each is its own cycle.
