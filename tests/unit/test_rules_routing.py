from llmwiki.rules.palette import ConfidenceRoute, Accept
from llmwiki.rules.base import EvalContext, get_rule, known_rules
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.models import IncomingDocument


def ctx(findings=None):
    c = EvalContext(doc=IncomingDocument(collection="kb", content="body"),
                    provider=FakeProvider(), config=CollectionConfig(), candidates_loader=lambda: [])
    c.findings.update(findings or {})
    return c


def test_accept_is_terminal():
    assert Accept().evaluate(ctx(), Accept.Params()).disposition == "ACCEPT"


def test_confidence_route_reviews_on_low():
    c = ctx({"confidence": "LOW"})
    r = ConfidenceRoute().evaluate(c, ConfidenceRoute.Params(min_confidence="HIGH", on_low="REVIEW"))
    assert r.disposition == "REVIEW"
    c2 = ctx({"confidence": "HIGH"})
    assert ConfidenceRoute().evaluate(c2, ConfidenceRoute.Params(min_confidence="HIGH")).disposition == "PASS"


def test_all_builtin_rules_registered():
    for rid in ["min_length", "content_type", "regex_denylist", "knowledge_worthiness",
                "exact_duplicate", "identity_match", "semantic_duplicate",
                "version_on_change", "semantic_replace", "confidence_route", "accept"]:
        assert rid in known_rules()
        assert get_rule(rid).id == rid
