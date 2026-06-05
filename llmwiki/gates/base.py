from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Protocol
from pydantic import BaseModel, Field
from llmwiki.config import CollectionConfig
from llmwiki.models import IncomingDocument
from llmwiki.providers.base import Provider

Decision = Literal["PASS", "REJECT", "REVIEW"]


class GateVerdict(BaseModel):
    decision: Decision
    gate: str
    reason: str = ""
    signals: dict = Field(default_factory=dict)


@dataclass
class GateContext:
    config: CollectionConfig
    provider: Provider


class Gate(Protocol):
    name: str
    def check(self, doc: IncomingDocument, ctx: GateContext) -> GateVerdict: ...
