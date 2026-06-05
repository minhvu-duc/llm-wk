from llmwiki.pipeline import IngestService
from llmwiki.storage import IndexStore
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import KnowledgeVerdict
from llmwiki.models import IncomingDocument, Outcome


class SpyProvider(FakeProvider):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.embed_calls = 0

    def embed(self, text):
        self.embed_calls += 1
        return super().embed(text)


def make_service(tmp_path, provider):
    svc = IngestService(IndexStore(str(tmp_path / "idx.db")), str(tmp_path / "r"), provider)
    svc.ensure_collection("kb")
    return svc


def ingest(svc, content, **kw):
    return svc.ingest(IncomingDocument(collection="kb", content=content, **kw))


def test_short_junk_rejected_without_embedding(tmp_path):
    prov = SpyProvider()
    svc = make_service(tmp_path, prov)
    rec = ingest(svc, "ok thanks")
    assert rec.outcome is Outcome.REJECTED
    assert rec.signals.get("rule") == "min_length"
    assert prov.embed_calls == 0          # rejected before embedding


def test_knowledge_gate_reject_high_confidence(tmp_path):
    prov = SpyProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=False, confidence="HIGH")])
    svc = make_service(tmp_path, prov)
    rec = ingest(svc, "this is a long enough sentence to clear the min info gate easily")
    assert rec.outcome is Outcome.REJECTED and rec.signals.get("rule") == "knowledge_worthiness"


def test_borderline_routes_to_review_with_item(tmp_path):
    prov = SpyProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=False, confidence="MEDIUM")])
    svc = make_service(tmp_path, prov)
    rec = ingest(svc, "this is a long enough sentence to clear the min info gate easily")
    assert rec.outcome is Outcome.NEEDS_REVIEW
    assert any(r.decision_id == rec.id for r in svc.index.list_reviews("kb"))


def test_real_knowledge_passes_to_new(tmp_path):
    prov = SpyProvider(assess_verdicts=[KnowledgeVerdict(is_knowledge=True, confidence="HIGH")])
    svc = make_service(tmp_path, prov)
    rec = ingest(svc, "The enterprise refund window is thirty days from the invoice date.")
    assert rec.outcome is Outcome.NEW and prov.embed_calls == 1


def test_validity_omitted_pipeline_bypasses_gates(tmp_path):
    prov = SpyProvider()
    svc = make_service(tmp_path, prov)
    # a pipeline without a validity gate -> short content is no longer filtered
    svc.index.set_collection_config("kb", {"pipeline": [
        {"gate": "dedup", "rules": [{"type": "exact_duplicate"}]}]})
    rec = ingest(svc, "ok thanks")        # would be rejected by the default validity gate
    assert rec.outcome is Outcome.NEW
