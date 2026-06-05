from llmwiki.storage.index_store import IndexStore
from llmwiki.models import Document, DocumentVersion, DecisionRecord, Outcome


def make_store(tmp_path):
    return IndexStore(str(tmp_path / "idx.db"))


def test_collection_create_and_get(tmp_path):
    s = make_store(tmp_path)
    s.create_collection("kb")
    assert s.get_collection("kb") is not None
    assert s.get_collection("missing") is None


def test_set_collection_config_roundtrip(tmp_path):
    s = make_store(tmp_path); s.create_collection("kb")
    s.set_collection_config("kb", {"min_chars": 99, "quality_enabled": False})
    cfg = s.get_collection("kb")["config"]
    assert cfg["min_chars"] == 99 and cfg["quality_enabled"] is False


def test_document_and_version_persist(tmp_path):
    s = make_store(tmp_path); s.create_collection("kb")
    doc = Document(id="d1", collection="kb", stable_identity="uri://x")
    s.save_document(doc)
    v = DocumentVersion(id="v1", document_id="d1", content_hash="h1")
    s.save_version(v, embedding=[0.1, 0.2], shingles={"a b c"})
    s.set_current_version("d1", "v1")
    got = s.get_document("d1")
    assert got.current_version_id == "v1"


def test_find_by_identity(tmp_path):
    s = make_store(tmp_path); s.create_collection("kb")
    s.save_document(Document(id="d1", collection="kb", stable_identity="uri://x"))
    assert s.find_by_identity("kb", "uri://x").id == "d1"
    assert s.find_by_identity("kb", "uri://none") is None


def test_candidates_returns_embeddings(tmp_path):
    s = make_store(tmp_path); s.create_collection("kb")
    s.save_document(Document(id="d1", collection="kb", stable_identity="i1"))
    s.save_version(DocumentVersion(id="v1", document_id="d1", content_hash="h1"),
                   embedding=[1.0, 0.0], shingles={"a b c"}, content="hello")
    s.set_current_version("d1", "v1")
    cands = s.current_candidates("kb")
    assert len(cands) == 1
    assert cands[0].document_id == "d1" and cands[0].embedding == [1.0, 0.0]


def test_decision_and_idempotency(tmp_path):
    s = make_store(tmp_path); s.create_collection("kb")
    rec = DecisionRecord(id="dec1", collection="kb", outcome=Outcome.NEW, content_hash="h1")
    s.save_decision(rec, idempotency_key="k1")
    assert s.get_decision_by_idempotency("kb", "k1").id == "dec1"
    assert s.get_decision_by_idempotency("kb", "kX") is None
    assert s.get_decision("dec1").outcome is Outcome.NEW
