from llmwiki.classifier import classify, Candidate, Fingerprint
from llmwiki.config import CollectionConfig
from llmwiki.models import Outcome
from llmwiki.providers.base import AdjudicatorVerdict

CFG = CollectionConfig()


def fp(hash_="h_new", embed=(1.0, 0.0), shingles=frozenset({"a b c"}), declared_id=None):
    return Fingerprint(content_hash=hash_, embedding=list(embed),
                       shingles=set(shingles), declared_id=declared_id)


def cand(doc_id, hash_, embed, shingles=frozenset({"a b c"}), content="x"):
    return Candidate(document_id=doc_id, content_hash=hash_, embedding=list(embed),
                     shingles=set(shingles), content=content, identity_match=False)


def adj_same(*_): return AdjudicatorVerdict(relationship="SAME_UPDATED", confidence="HIGH")
def adj_diff(*_): return AdjudicatorVerdict(relationship="DIFFERENT", confidence="HIGH")


def test_id_match_same_hash_is_duplicate():
    c = cand("d1", "h1", (1.0, 0.0)); c.identity_match = True
    d = classify(fp(hash_="h1", declared_id="d1"), [c], CFG, adj_diff)
    assert d.outcome is Outcome.DUPLICATE and d.document_id == "d1"


def test_id_match_diff_hash_is_update():
    c = cand("d1", "h_old", (1.0, 0.0)); c.identity_match = True
    d = classify(fp(hash_="h_new", declared_id="d1"), [c], CFG, adj_diff)
    assert d.outcome is Outcome.UPDATE and d.document_id == "d1"


def test_no_candidates_is_new():
    d = classify(fp(), [], CFG, adj_diff)
    assert d.outcome is Outcome.NEW


def test_low_similarity_is_new_without_llm():
    called = {"n": 0}
    def adj(*_): called["n"] += 1; return adj_diff()
    c = cand("d1", "h_old", (0.0, 1.0))  # orthogonal -> sim 0
    d = classify(fp(embed=(1.0, 0.0)), [c], CFG, adj)
    assert d.outcome is Outcome.NEW and called["n"] == 0


def test_high_similarity_same_hash_is_duplicate_without_llm():
    called = {"n": 0}
    def adj(*_): called["n"] += 1; return adj_diff()
    c = cand("d1", "hX", (1.0, 0.0))
    d = classify(fp(hash_="hX", embed=(1.0, 0.0)), [c], CFG, adj)
    assert d.outcome is Outcome.DUPLICATE and called["n"] == 0


def test_high_similarity_diff_hash_consistent_shingles_is_update():
    c = cand("d1", "h_old", (1.0, 0.0), shingles=frozenset({"a b c"}))
    d = classify(fp(hash_="h_new", embed=(1.0, 0.0), shingles=frozenset({"a b c"})), [c], CFG, adj_same)
    assert d.outcome is Outcome.UPDATE and d.document_id == "d1"


def test_gray_band_escalates_and_low_confidence_needs_review():
    def adj_low(*_): return AdjudicatorVerdict(relationship="SAME_UPDATED", confidence="LOW")
    c = cand("d1", "h_old", (0.9, 0.436))  # sim ~0.9 -> gray band
    d = classify(fp(embed=(1.0, 0.0)), [c], CFG, adj_low)
    assert d.outcome is Outcome.NEEDS_REVIEW


def test_gray_band_related_but_distinct_needs_review():
    def adj_rel(*_): return AdjudicatorVerdict(relationship="RELATED_BUT_DISTINCT", confidence="HIGH")
    c = cand("d1", "h_old", (0.9, 0.436))
    d = classify(fp(embed=(1.0, 0.0)), [c], CFG, adj_rel)
    assert d.outcome is Outcome.NEEDS_REVIEW


def test_small_top2_margin_needs_review():
    c1 = cand("d1", "h1", (1.0, 0.0))
    c2 = cand("d2", "h2", (0.999, 0.0447))  # nearly tied with c1
    d = classify(fp(embed=(1.0, 0.0)), [c1, c2], CFG, adj_same)
    assert d.outcome is Outcome.NEEDS_REVIEW


def test_conflicting_signals_high_sim_low_lexical_needs_review():
    # embedding ~1.0 but disjoint shingles -> conflicting -> review
    c = cand("d1", "h_old", (1.0, 0.0), shingles=frozenset({"x y z"}))
    d = classify(fp(hash_="h_new", embed=(1.0, 0.0), shingles=frozenset({"a b c"})), [c], CFG, adj_same)
    assert d.outcome is Outcome.NEEDS_REVIEW


def test_adjudication_disabled_gray_band_needs_review():
    cfg = CollectionConfig(adjudication_enabled=False)
    c = cand("d1", "h_old", (0.9, 0.436))
    d = classify(fp(embed=(1.0, 0.0)), [c], cfg, adj_same)
    assert d.outcome is Outcome.NEEDS_REVIEW
