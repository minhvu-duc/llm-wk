from __future__ import annotations
from typing import Protocol, Literal
from pydantic import BaseModel

Relationship = Literal["SAME_UPDATED", "DIFFERENT", "RELATED_BUT_DISTINCT", "CONFLICTING"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]


class AdjudicatorVerdict(BaseModel):
    relationship: Relationship
    confidence: Confidence
    rationale: str = ""


class Provider(Protocol):
    def embed(self, text: str) -> list[float]: ...
    def adjudicate(self, incoming: str, existing: str) -> AdjudicatorVerdict: ...
