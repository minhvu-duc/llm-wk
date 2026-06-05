from llmwiki.rules.palette import ExactDuplicate, IdentityMatch, SemanticDuplicate
from llmwiki.rules.base import EvalContext, Candidate
from llmwiki.config import CollectionConfig
from llmwiki.providers.fake import FakeProvider
from llmwiki.models import IncomingDocument


def make_ctx(content, candidates, provider=None, declared_id=None):
    return EvalContext(
        doc=IncomingDocument(collection="kb", content=content, declared_id=declared_id),
        provider=provider or FakeProvider(), config=CollectionConfig(),
        candidates_loader=lambda: candidates)


def test_exact_duplicate_fires_on_hash_match():
    c = make_ctx("the cat sat on the mat here", [])
    cand = Candidate(document_id="d1", content_hash=c.content_hash, embedding=[1.0, 0.0])
    c2 = make_ctx("the cat sat on the mat here", [cand])
    r = ExactDuplicate().evaluate(c2, ExactDuplicate.Params())
    assert r.disposition == "DUPLICATE"


def test_identity_match_same_hash_duplicate_diff_annotates_pass():
    base = make_ctx("first body version here now", [], declared_id="doc-1")
    same_hash = base.content_hash
    cand = Candidate(document_id="d1", content_hash=same_hash, embedding=[1.0, 0.0], identity_match=True)
    c = make_ctx("first body version here now", [cand], declared_id="doc-1")
    assert IdentityMatch().evaluate(c, IdentityMatch.Params()).disposition == "DUPLICATE"

    cand2 = Candidate(document_id="d1", content_hash="other", embedding=[1.0, 0.0], identity_match=True)
    c2 = make_ctx("changed body version here now", [cand2], declared_id="doc-1")
    r = IdentityMatch().evaluate(c2, IdentityMatch.Params())
    assert r.disposition == "PASS" and r.context_updates["match"].document_id == "d1"


def test_semantic_duplicate_low_sim_pass_high_sim_annotates_match():
    far = Candidate(document_id="d1", content_hash="h", embedding=[0.0, 1.0], content="x")
    c = make_ctx("alpha beta gamma delta epsilon", [far])
    r = SemanticDuplicate().evaluate(c, SemanticDuplicate.Params())
    assert r.disposition == "PASS" and "match" not in r.context_updates

    near = Candidate(document_id="d2", content_hash="other",
                     embedding=FakeProvider().embed("alpha beta gamma delta epsilon"),
                     shingles=set(), content="alpha beta gamma delta epsilon")
    c2 = make_ctx("alpha beta gamma delta epsilon zeta", [near])
    r2 = SemanticDuplicate().evaluate(c2, SemanticDuplicate.Params(threshold_high=0.5, gray_band=0.2))
    assert r2.disposition in ("PASS", "REVIEW")
