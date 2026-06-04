import threading
from llmwiki.coordinator import Coordinator
from llmwiki.pipeline import IngestService
from llmwiki.storage import IndexStore
from llmwiki.providers.fake import FakeProvider
from llmwiki.models import IncomingDocument, Outcome


def test_concurrent_same_doc_serialized_no_dup_documents(tmp_path):
    svc = IngestService(IndexStore(str(tmp_path / "idx.db")), str(tmp_path / "r"), FakeProvider())
    svc.ensure_collection("kb")
    coord = Coordinator(svc)
    results = []

    def go():
        results.append(coord.ingest(IncomingDocument(
            collection="kb", content="same body here", declared_id="doc-1")))

    threads = [threading.Thread(target=go) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    outcomes = sorted(r.outcome for r in results)
    # exactly one NEW; the rest DUPLICATE (never two NEWs racing)
    assert outcomes.count(Outcome.NEW) == 1
    assert all(o in (Outcome.NEW, Outcome.DUPLICATE) for o in outcomes)
