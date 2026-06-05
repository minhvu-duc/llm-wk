from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import KnowledgeVerdict

RUBRIC = "keep facts, drop greetings"


def test_fake_assess_greeting_is_not_knowledge():
    v = FakeProvider().assess("hi thanks", RUBRIC)
    assert isinstance(v, KnowledgeVerdict)
    assert v.is_knowledge is False and v.confidence == "HIGH"


def test_fake_assess_substantive_is_knowledge():
    v = FakeProvider().assess(
        "The refund window for enterprise customers is thirty days from invoice date.", RUBRIC)
    assert v.is_knowledge is True


def test_fake_assess_scripted_overrides_heuristic():
    scripted = KnowledgeVerdict(is_knowledge=False, category="x", confidence="MEDIUM")
    p = FakeProvider(assess_verdicts=[scripted])
    assert p.assess("a long substantive sentence about refunds and policies", RUBRIC).confidence == "MEDIUM"
