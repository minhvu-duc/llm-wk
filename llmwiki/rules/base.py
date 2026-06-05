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
