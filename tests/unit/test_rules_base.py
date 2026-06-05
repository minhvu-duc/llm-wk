from llmwiki.rules.base import (Candidate, EvalContext, RuleResult, cosine,
                                register_rule, get_rule, known_rules)
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.models import IncomingDocument


def test_cosine_basic():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_context_memoizes_hash_and_candidates():
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return [Candidate(document_id="d1", content_hash="h", embedding=[1.0, 0.0])]

    ctx = EvalContext(doc=IncomingDocument(collection="kb", content="hello world facts here"),
                      provider=FakeProvider(), config=CollectionConfig(), candidates_loader=loader)
    assert len(ctx.content_hash) == 64
    ctx.candidates(); ctx.candidates()
    assert calls["n"] == 1  # memoized
    assert isinstance(ctx.embedding, list)


def test_registry_round_trip():
    from pydantic import BaseModel

    class Dummy:
        id = "dummy"; category = "validity"; kind = "deterministic"

        class Params(BaseModel):
            pass

        def evaluate(self, ctx, params):
            return RuleResult(disposition="PASS")

    register_rule(Dummy())
    assert "dummy" in known_rules()
    assert get_rule("dummy").category == "validity"
