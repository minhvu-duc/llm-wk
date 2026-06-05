from __future__ import annotations
from llmwiki.gates.base import Gate, GateVerdict, GateContext
from llmwiki.gates.builtin import MinInfoGate, DenylistGate, KnowledgeWorthinessGate
from llmwiki.config import CollectionConfig
from llmwiki.models import IncomingDocument

_REGISTRY: dict[str, type] = {
    "min_info": MinInfoGate,
    "denylist": DenylistGate,
    "knowledge": KnowledgeWorthinessGate,
}


def register_gate(name: str, cls: type) -> None:
    _REGISTRY[name] = cls


def known_gates() -> set[str]:
    return set(_REGISTRY)


class GateChain:
    def __init__(self, gates: list[Gate]):
        self.gates = gates

    def run(self, doc: IncomingDocument, ctx: GateContext) -> GateVerdict | None:
        for gate in self.gates:
            verdict = gate.check(doc, ctx)
            if verdict.decision != "PASS":
                return verdict
        return None


def build_chain(config: CollectionConfig) -> GateChain:
    gates: list[Gate] = []
    for name in config.gate_order:
        cls = _REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"unknown gate '{name}'")
        gates.append(cls())
    return GateChain(gates)
