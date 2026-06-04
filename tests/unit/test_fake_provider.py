import pytest
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import AdjudicatorVerdict


def test_embed_is_deterministic_and_unit_length():
    p = FakeProvider()
    v1 = p.embed("hello world")
    v2 = p.embed("hello world")
    assert v1 == v2
    assert abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-6


def test_identical_text_more_similar_than_different():
    p = FakeProvider()
    def cos(a, b): return sum(x * y for x, y in zip(a, b))
    base = p.embed("the cat sat on the mat")
    same = p.embed("the cat sat on the mat")
    diff = p.embed("quantum chromodynamics lecture notes")
    assert cos(base, same) == pytest.approx(1.0)
    assert cos(base, diff) < 0.95


def test_scripted_adjudicate():
    p = FakeProvider(verdicts=[AdjudicatorVerdict(relationship="SAME_UPDATED", confidence="HIGH")])
    v = p.adjudicate("a", "b")
    assert v.relationship == "SAME_UPDATED" and v.confidence == "HIGH"
