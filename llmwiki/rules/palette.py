from __future__ import annotations
import re
from typing import Literal
from pydantic import BaseModel, Field
from llmwiki.rules.base import EvalContext, RuleResult, cosine, register_rule
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


# ---------- Existence ----------

def _ranked(ctx: EvalContext):
    cands = ctx.candidates()
    return sorted(((c, cosine(ctx.embedding, c.embedding)) for c in cands),
                  key=lambda t: t[1], reverse=True)


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


# ---------- Update / Replace ----------

def _direction_signal(ctx: EvalContext, candidate) -> bool:
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

for _rule in (MinLength(), ContentType(), RegexDenylist(), KnowledgeWorthiness(),
              ExactDuplicate(), IdentityMatch(), SemanticDuplicate(),
              VersionOnChange(), SemanticReplace(), ConfidenceRoute(), Accept()):
    register_rule(_rule)
