import pytest
from llmwiki.gates.base import GateVerdict, GateContext
from llmwiki.gates.builtin import MinInfoGate, DenylistGate, KnowledgeWorthinessGate
from llmwiki.gates.chain import GateChain, build_chain, register_gate
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import KnowledgeVerdict
from llmwiki.models import IncomingDocument


def _doc(content):
    return IncomingDocument(collection="kb", content=content)


def _ctx(**overrides):
    return GateContext(config=CollectionConfig(**overrides), provider=FakeProvider())


# --- base ---

def test_gate_verdict_defaults():
    v = GateVerdict(decision="PASS", gate="x")
    assert v.reason == "" and v.signals == {}


def test_gate_context_holds_config_and_provider():
    ctx = GateContext(config=CollectionConfig(), provider=FakeProvider())
    assert ctx.config.min_chars == 40
    assert hasattr(ctx.provider, "assess")


# --- built-in gates ---

def test_min_info_rejects_short_and_passes_long():
    assert MinInfoGate().check(_doc("ok thanks"), _ctx()).decision == "REJECT"
    long = "The enterprise refund window is thirty days from the invoice date."
    assert MinInfoGate().check(_doc(long), _ctx()).decision == "PASS"


def test_denylist_matches_with_configured_action():
    ctx = _ctx(denylist_patterns=[r"\bSSN\b"], denylist_action="REJECT")
    assert DenylistGate().check(_doc("user SSN is 123"), ctx).decision == "REJECT"
    assert DenylistGate().check(_doc("nothing sensitive here at all"), ctx).decision == "PASS"


def test_denylist_default_action_is_review():
    ctx = _ctx(denylist_patterns=[r"password"])
    assert DenylistGate().check(_doc("the password is hunter2 again"), ctx).decision == "REVIEW"


def _ctx_assess(verdict):
    return GateContext(config=CollectionConfig(),
                       provider=FakeProvider(assess_verdicts=[verdict]))


def test_knowledge_gate_routes_by_verdict():
    g = KnowledgeWorthinessGate()
    passv = g.check(_doc("x" * 50), _ctx_assess(KnowledgeVerdict(is_knowledge=True, confidence="HIGH")))
    assert passv.decision == "PASS"
    rej = g.check(_doc("x" * 50), _ctx_assess(KnowledgeVerdict(is_knowledge=False, confidence="HIGH")))
    assert rej.decision == "REJECT"
    rev = g.check(_doc("x" * 50), _ctx_assess(KnowledgeVerdict(is_knowledge=False, confidence="LOW")))
    assert rev.decision == "REVIEW"


def test_knowledge_gate_provider_error_is_review():
    class Boom(FakeProvider):
        def assess(self, text, rubric):
            raise RuntimeError("provider down")
    ctx = GateContext(config=CollectionConfig(), provider=Boom())
    assert KnowledgeWorthinessGate().check(_doc("x" * 50), ctx).decision == "REVIEW"


# --- chain ---

def test_chain_returns_first_non_pass_and_short_circuits():
    calls = []

    class Tag:
        def __init__(self, name, decision):
            self.name = name
            self._d = decision
        def check(self, doc, ctx):
            calls.append(self.name)
            return GateVerdict(decision=self._d, gate=self.name)

    chain = GateChain([Tag("a", "PASS"), Tag("b", "REJECT"), Tag("c", "PASS")])
    v = chain.run(_doc("hi"), _ctx())
    assert v.decision == "REJECT" and v.gate == "b"
    assert calls == ["a", "b"]  # c never ran


def test_chain_all_pass_returns_none():
    class P:
        name = "p"
        def check(self, doc, ctx):
            return GateVerdict(decision="PASS", gate="p")
    assert GateChain([P()]).run(_doc("hi"), _ctx()) is None


def test_build_chain_from_config_order():
    cfg = CollectionConfig(gate_order=["denylist", "min_info"])
    chain = build_chain(cfg)
    assert [g.name for g in chain.gates] == ["denylist", "min_info"]


def test_build_chain_unknown_gate_raises():
    with pytest.raises(ValueError):
        build_chain(CollectionConfig(gate_order=["nope"]))


def test_register_custom_gate():
    class Custom:
        name = "custom"
        def check(self, doc, ctx):
            return GateVerdict(decision="PASS", gate="custom")
    register_gate("custom", Custom)
    chain = build_chain(CollectionConfig(gate_order=["custom"]))
    assert chain.gates[0].name == "custom"
