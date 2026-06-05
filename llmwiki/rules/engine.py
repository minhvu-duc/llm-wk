from __future__ import annotations
from dataclasses import dataclass, field
from llmwiki.rules.base import EvalContext, Disposition, get_rule


@dataclass
class CompiledRule:
    rule: object
    params: object


@dataclass
class CompiledGate:
    name: str
    rules: list[CompiledRule]


@dataclass
class EngineDecision:
    disposition: Disposition
    document_id: str | None = None
    reason: str = ""
    signals: dict = field(default_factory=dict)


def build_pipeline(pipeline_config: list[dict]) -> list[CompiledGate]:
    gates: list[CompiledGate] = []
    for gate in pipeline_config:
        compiled: list[CompiledRule] = []
        for rule_cfg in gate.get("rules", []):
            rule = get_rule(rule_cfg["type"])          # raises ValueError on unknown
            params = rule.Params(**rule_cfg.get("params", {}))
            compiled.append(CompiledRule(rule=rule, params=params))
        gates.append(CompiledGate(name=gate.get("gate", ""), rules=compiled))
    return gates


def evaluate_pipeline(gates: list[CompiledGate], ctx: EvalContext) -> EngineDecision:
    for gate in gates:
        for cr in gate.rules:
            result = cr.rule.evaluate(ctx, cr.params)
            if result.context_updates:
                ctx.findings.update(result.context_updates)
            if result.disposition != "PASS":
                return EngineDecision(disposition=result.disposition,
                                      document_id=result.signals.get("document_id"),
                                      reason=f"{gate.name}:{cr.rule.id}",
                                      signals=result.signals)
    return EngineDecision(disposition="ACCEPT", reason="default-accept")
