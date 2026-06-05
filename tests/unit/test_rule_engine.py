import pytest
from llmwiki.rules.engine import evaluate_pipeline, build_pipeline, EngineDecision
from llmwiki.rules.base import EvalContext, Candidate
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.models import IncomingDocument


def ctx(content, candidates=None, provider=None, declared_id=None):
    return EvalContext(doc=IncomingDocument(collection="kb", content=content, declared_id=declared_id),
                       provider=provider or FakeProvider(), config=CollectionConfig(),
                       candidates_loader=lambda: candidates or [])


PIPE = [
    {"gate": "validity", "rules": [{"type": "min_length", "params": {"min_chars": 40}}]},
    {"gate": "dedup", "rules": [{"type": "exact_duplicate"}, {"type": "semantic_duplicate"}]},
    {"gate": "update", "rules": [{"type": "version_on_change"}]},
]


def test_short_content_rejected_first_gate():
    d = evaluate_pipeline(build_pipeline(PIPE), ctx("too short"))
    assert d.disposition == "REJECT"


def test_no_match_accepts():
    d = evaluate_pipeline(build_pipeline(PIPE), ctx("a genuinely long enough body of text here now"))
    assert d.disposition == "ACCEPT"


def test_exact_duplicate_short_circuits():
    c = ctx("a genuinely long enough body of text here now")
    cand = Candidate(document_id="d1", content_hash=c.content_hash, embedding=[1.0, 0.0])
    c2 = ctx("a genuinely long enough body of text here now", candidates=[cand])
    d = evaluate_pipeline(build_pipeline(PIPE), c2)
    assert d.disposition == "DUPLICATE" and d.document_id == "d1"


def test_context_flow_existence_to_update():
    text = "a genuinely long enough body of text here now"
    cand = Candidate(document_id="d1", content_hash="old", embedding=[1.0, 0.0], identity_match=True)
    pipe = [
        {"gate": "dedup", "rules": [{"type": "identity_match"}]},
        {"gate": "update", "rules": [{"type": "version_on_change"}]},
    ]
    d = evaluate_pipeline(build_pipeline(pipe), ctx(text, candidates=[cand], declared_id="doc-1"))
    assert d.disposition == "UPDATE" and d.document_id == "d1"


def test_build_pipeline_unknown_rule_raises():
    with pytest.raises(ValueError):
        build_pipeline([{"gate": "g", "rules": [{"type": "nope"}]}])
