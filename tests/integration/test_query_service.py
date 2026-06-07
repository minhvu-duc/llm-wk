from llmwiki.pipeline import IngestService
from llmwiki.query import QueryService
from llmwiki.storage import IndexStore
from llmwiki.providers.fake import FakeProvider
from llmwiki.config import CollectionConfig
from llmwiki.models import IncomingDocument

DEDUP_ONLY = CollectionConfig(pipeline=[
    {"gate": "dedup", "rules": [{"type": "exact_duplicate"}, {"type": "identity_match"},
                                {"type": "semantic_duplicate"}]},
    {"gate": "update", "rules": [{"type": "version_on_change"}]},
])


def setup(tmp_path):
    idx = IndexStore(str(tmp_path / "i.db"))
    prov = FakeProvider()
    svc = IngestService(idx, str(tmp_path / "r"), prov, config=DEDUP_ONLY)
    for c in ("kb", "hr"):
        svc.ensure_collection(c)
    return idx, prov, svc


def ing(svc, collection, content, **kw):
    return svc.ingest(IncomingDocument(collection=collection, content=content, **kw))


def test_query_zone_ranks_by_similarity(tmp_path):
    idx, prov, svc = setup(tmp_path)
    ing(svc, "kb", "the cat sat on the warm mat", declared_id="a")
    ing(svc, "kb", "quantum chromodynamics field theory lecture", declared_id="b")
    q = QueryService(idx, prov)
    results = q.query_zone("kb", "a cat on a mat", top_k=2)
    assert results[0]["document_id"] is not None
    assert results[0]["score"] >= results[-1]["score"]
    # the cat document should rank above the physics one
    assert "cat" in results[0]["snippet"]


def test_query_global_spans_zones_and_resolves_scope(tmp_path):
    idx, prov, svc = setup(tmp_path)
    ing(svc, "kb", "the cat sat on the warm mat", declared_id="a")
    ing(svc, "hr", "the cat policy for office pets", declared_id="b")
    q = QueryService(idx, prov)
    zones = q.resolve_zones(["*"], None)
    assert set(zones) == {"hr", "kb"}
    results = q.query_global(zones, "cat", top_k=5)
    cols = {r["collection"] for r in results}
    assert cols == {"kb", "hr"}


def test_resolve_zones_respects_allowance_and_request(tmp_path):
    idx, prov, svc = setup(tmp_path)
    ing(svc, "kb", "x" * 40, declared_id="a")
    q = QueryService(idx, prov)
    assert q.resolve_zones(["kb"], None) == ["kb"]          # not allowed hr
    assert q.resolve_zones(["kb", "hr"], ["hr"]) == ["hr"]  # request narrows
    assert q.resolve_zones(["kb"], ["hr"]) == []            # requested but not allowed
