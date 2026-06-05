from llmwiki.rules.palette import MinLength, RegexDenylist, KnowledgeWorthiness, ContentType
from llmwiki.rules.base import EvalContext
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import KnowledgeVerdict
from llmwiki.models import IncomingDocument


def ctx(content, provider=None, content_type="text/plain"):
    return EvalContext(doc=IncomingDocument(collection="kb", content=content, content_type=content_type),
                       provider=provider or FakeProvider(), config=CollectionConfig())


def run(rule, c, **params):
    return rule.evaluate(c, rule.Params(**params))


def test_min_length():
    assert run(MinLength(), ctx("ok"), min_chars=40).disposition == "REJECT"
    assert run(MinLength(), ctx("x" * 50), min_chars=40).disposition == "PASS"


def test_content_type():
    assert run(ContentType(), ctx("hi", content_type="application/zip"),
               allowed=["text/plain"]).disposition == "REJECT"
    assert run(ContentType(), ctx("hi", content_type="text/plain"),
               allowed=["text/plain"]).disposition == "PASS"


def test_regex_denylist():
    r = run(RegexDenylist(), ctx("my SSN is x"), patterns=[r"\bSSN\b"], action="REVIEW")
    assert r.disposition == "REVIEW"
    assert run(RegexDenylist(), ctx("nothing here"), patterns=[r"\bSSN\b"], action="REVIEW").disposition == "PASS"


def test_knowledge_worthiness_routes_by_verdict():
    kw = KnowledgeWorthiness()
    p = FakeProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=False, confidence="HIGH")])
    assert kw.evaluate(ctx("x" * 50, p), kw.Params(rubric="r", on_uncertain="REVIEW")).disposition == "REJECT"
    p2 = FakeProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=False, confidence="LOW")])
    assert kw.evaluate(ctx("x" * 50, p2), kw.Params(rubric="r", on_uncertain="REVIEW")).disposition == "REVIEW"
    p3 = FakeProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=True, confidence="HIGH")])
    assert kw.evaluate(ctx("x" * 50, p3), kw.Params(rubric="r", on_uncertain="REVIEW")).disposition == "PASS"
