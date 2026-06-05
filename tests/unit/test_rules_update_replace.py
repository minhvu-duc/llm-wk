from llmwiki.rules.palette import VersionOnChange, SemanticReplace
from llmwiki.rules.base import EvalContext, Candidate
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import AdjudicatorVerdict
from llmwiki.models import IncomingDocument


def ctx(content, findings, provider=None, metadata=None):
    c = EvalContext(doc=IncomingDocument(collection="kb", content=content, metadata=metadata or {}),
                    provider=provider or FakeProvider(), config=CollectionConfig(),
                    candidates_loader=lambda: [])
    c.findings.update(findings)
    return c


def test_version_on_change_updates_when_match_present():
    m = Candidate(document_id="d1", content_hash="old", embedding=[1.0, 0.0])
    r = VersionOnChange().evaluate(ctx("new body", {"match": m}), VersionOnChange.Params())
    assert r.disposition == "UPDATE" and r.signals["document_id"] == "d1"


def test_version_on_change_passes_without_match():
    assert VersionOnChange().evaluate(ctx("body", {}), VersionOnChange.Params()).disposition == "PASS"


def test_semantic_replace_supersedes_with_signal():
    top = Candidate(document_id="old1", content_hash="h", embedding=[1.0, 0.0], content="old")
    prov = FakeProvider(verdicts=[AdjudicatorVerdict(relationship="SUPERSEDES", confidence="HIGH")])
    c = ctx("new", {"top_candidate": top, "top_sim": 0.95}, provider=prov,
            metadata={"supersedes": "old1"})
    r = SemanticReplace().evaluate(c, SemanticReplace.Params(threshold=0.9))
    assert r.disposition == "REPLACE" and r.signals["document_id"] == "old1"


def test_semantic_replace_no_signal_reviews_by_default():
    top = Candidate(document_id="old1", content_hash="h", embedding=[1.0, 0.0], content="old")
    prov = FakeProvider(verdicts=[AdjudicatorVerdict(relationship="SUPERSEDES", confidence="HIGH")])
    c = ctx("new", {"top_candidate": top, "top_sim": 0.95}, provider=prov)
    r = SemanticReplace().evaluate(c, SemanticReplace.Params(threshold=0.9))
    assert r.disposition == "REVIEW"


def test_semantic_replace_unsignaled_allowed_when_flag_on():
    top = Candidate(document_id="old1", content_hash="h", embedding=[1.0, 0.0], content="old")
    prov = FakeProvider(verdicts=[AdjudicatorVerdict(relationship="SUPERSEDES", confidence="HIGH")])
    c = ctx("new", {"top_candidate": top, "top_sim": 0.95}, provider=prov)
    r = SemanticReplace().evaluate(c, SemanticReplace.Params(threshold=0.9, allow_unsignaled_replace=True))
    assert r.disposition == "REPLACE"


def test_semantic_replace_low_confidence_reviews():
    top = Candidate(document_id="old1", content_hash="h", embedding=[1.0, 0.0], content="old")
    prov = FakeProvider(verdicts=[AdjudicatorVerdict(relationship="SUPERSEDES", confidence="LOW")])
    c = ctx("new", {"top_candidate": top, "top_sim": 0.95}, provider=prov, metadata={"supersedes": "old1"})
    assert SemanticReplace().evaluate(c, SemanticReplace.Params(threshold=0.9)).disposition == "REVIEW"


def test_semantic_replace_no_candidate_passes():
    assert SemanticReplace().evaluate(ctx("new", {}), SemanticReplace.Params()).disposition == "PASS"
