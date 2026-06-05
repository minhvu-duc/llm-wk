from __future__ import annotations
import re
from llmwiki.gates.base import GateVerdict, GateContext
from llmwiki.models import IncomingDocument
from llmwiki.text import normalize


class MinInfoGate:
    name = "min_info"

    def check(self, doc: IncomingDocument, ctx: GateContext) -> GateVerdict:
        n = len(normalize(doc.content))
        if n < ctx.config.min_chars:
            return GateVerdict(decision="REJECT", gate=self.name,
                               reason=f"below min_chars ({n} < {ctx.config.min_chars})",
                               signals={"length": n})
        return GateVerdict(decision="PASS", gate=self.name, signals={"length": n})


class DenylistGate:
    name = "denylist"

    def check(self, doc: IncomingDocument, ctx: GateContext) -> GateVerdict:
        for pattern in ctx.config.denylist_patterns:
            if re.search(pattern, doc.content):
                return GateVerdict(decision=ctx.config.denylist_action, gate=self.name,
                                   reason=f"matched denylist /{pattern}/",
                                   signals={"pattern": pattern})
        return GateVerdict(decision="PASS", gate=self.name)


class KnowledgeWorthinessGate:
    name = "knowledge"

    def check(self, doc: IncomingDocument, ctx: GateContext) -> GateVerdict:
        try:
            v = ctx.provider.assess(doc.content, ctx.config.knowledge_rubric)
        except Exception as e:
            return GateVerdict(decision="REVIEW", gate=self.name,
                               reason="assessor error", signals={"error": str(e)})
        signals = {"category": v.category, "confidence": v.confidence,
                   "is_knowledge": v.is_knowledge}
        if v.is_knowledge:
            return GateVerdict(decision="PASS", gate=self.name, signals=signals)
        if v.confidence == "HIGH":
            return GateVerdict(decision="REJECT", gate=self.name,
                               reason=f"not knowledge ({v.category})", signals=signals)
        return GateVerdict(decision="REVIEW", gate=self.name,
                           reason="borderline knowledge", signals=signals)
