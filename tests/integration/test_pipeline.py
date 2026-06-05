from llmwiki.pipeline import IngestService
from llmwiki.storage import IndexStore
from llmwiki.providers.fake import FakeProvider
from llmwiki.providers.base import AdjudicatorVerdict
from llmwiki.models import IncomingDocument, Outcome


def make_service(tmp_path, provider=None):
    idx = IndexStore(str(tmp_path / "idx.db"))
    svc = IngestService(index=idx, content_root=str(tmp_path / "repos"),
                        provider=provider or FakeProvider())
    svc.ensure_collection("kb")
    return svc


def ingest(svc, content, **kw):
    return svc.ingest(IncomingDocument(collection="kb", content=content, **kw))


def test_load_config_returns_stored_then_default(tmp_path):
    svc = make_service(tmp_path)
    # no stored config -> default
    assert svc._load_config("kb").min_chars == 40
    svc.index.set_collection_config("kb", {"min_chars": 5})
    assert svc._load_config("kb").min_chars == 5


def test_first_doc_is_new(tmp_path):
    svc = make_service(tmp_path)
    rec = ingest(svc, "the cat sat on the mat", declared_id="doc-1")
    assert rec.outcome is Outcome.NEW
    assert rec.document_id is not None
    assert rec.resulting_version_id is not None


def test_same_id_same_content_is_duplicate(tmp_path):
    svc = make_service(tmp_path)
    ingest(svc, "hello world", declared_id="doc-1")
    rec = ingest(svc, "hello world", declared_id="doc-1")
    assert rec.outcome is Outcome.DUPLICATE


def test_same_id_changed_content_is_update(tmp_path):
    svc = make_service(tmp_path)
    ingest(svc, "version one body", declared_id="doc-1")
    rec = ingest(svc, "version two body different", declared_id="doc-1")
    assert rec.outcome is Outcome.UPDATE


def test_unrelated_doc_is_new(tmp_path):
    svc = make_service(tmp_path)
    ingest(svc, "the cat sat on the mat", declared_id="doc-1")
    rec = ingest(svc, "quantum chromodynamics field theory", declared_id="doc-2")
    assert rec.outcome is Outcome.NEW


def test_idempotency_key_returns_original_decision(tmp_path):
    svc = make_service(tmp_path)
    r1 = ingest(svc, "abc def ghi", idempotency_key="k1")
    r2 = ingest(svc, "abc def ghi", idempotency_key="k1")
    assert r1.id == r2.id


class _GrayProvider:
    """Forces a gray-band similarity (0.9) on the second doc + LOW confidence,
    so the decision must route to NEEDS_REVIEW deterministically."""

    def __init__(self):
        self._n = 0

    def embed(self, text):
        self._n += 1
        return [1.0, 0.0] if self._n == 1 else [0.9, 0.436]

    def adjudicate(self, incoming, existing):
        return AdjudicatorVerdict(relationship="SAME_UPDATED", confidence="LOW")


def test_needs_review_creates_review_item(tmp_path):
    svc = make_service(tmp_path, provider=_GrayProvider())
    ingest(svc, "alpha beta gamma delta")           # sim source -> (1.0, 0.0)
    rec = ingest(svc, "alpha beta gamma epsilon")    # -> (0.9, 0.436), cosine 0.9
    assert rec.outcome is Outcome.NEEDS_REVIEW
    reviews = svc.index.list_reviews("kb")
    assert any(r.decision_id == rec.id for r in reviews)
