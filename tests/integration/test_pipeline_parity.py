from llmwiki.pipeline import IngestService
from llmwiki.storage import IndexStore
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import AdjudicatorVerdict
from llmwiki.models import IncomingDocument, Outcome


def svc(tmp_path, provider=None):
    s = IngestService(IndexStore(str(tmp_path / "i.db")), str(tmp_path / "r"),
                      provider or FakeProvider())
    s.ensure_collection("kb")
    return s


def ing(s, content, **kw):
    return s.ingest(IncomingDocument(collection="kb", content=content, **kw))


def test_parity_new_dup_update_reject(tmp_path):
    s = svc(tmp_path)
    long = "The enterprise refund window is thirty days from the invoice date."
    assert ing(s, long, declared_id="d1").outcome is Outcome.NEW
    assert ing(s, long, declared_id="d1").outcome is Outcome.DUPLICATE
    assert ing(s, long + " Updated terms apply.", declared_id="d1").outcome is Outcome.UPDATE
    assert ing(s, "ok thanks").outcome is Outcome.REJECTED          # min_length


def test_replace_via_supersedes_hint(tmp_path):
    s = svc(tmp_path, provider=FakeProvider(
        verdicts=[AdjudicatorVerdict(relationship="SUPERSEDES", confidence="HIGH")]))
    # semantic_duplicate acts as a pure retriever here (high gray_band -> always annotate
    # top_candidate and PASS), so semantic_replace makes the supersession decision.
    s.index.set_collection_config("kb", {"pipeline": [
        {"gate": "dedup", "rules": [{"type": "semantic_duplicate",
                                     "params": {"threshold_high": 2.0, "gray_band": 2.0}}]},
        {"gate": "update", "rules": [{"type": "semantic_replace",
                                      "params": {"threshold": 0.0}}]},
    ]})
    old = ing(s, "The old refund policy is sixty days.", declared_id="p-old")
    assert old.outcome is Outcome.NEW
    rec = ing(s, "The refund policy is now thirty days, replacing the old one.",
              metadata={"supersedes": old.document_id})
    assert rec.outcome is Outcome.REPLACE
    assert s.index.get_document(old.document_id).status == "replaced"
