from llmwiki.models import Outcome, IncomingDocument, DecisionRecord, DocumentVersion


def test_outcome_values():
    assert {o.value for o in Outcome} == {
        "REJECTED", "DUPLICATE", "UPDATE", "NEW", "NEEDS_REVIEW", "REPLACE"
    }


def test_outcome_has_replace():
    assert Outcome.REPLACE.value == "REPLACE"


def test_incoming_document_defaults():
    doc = IncomingDocument(collection="kb", content="hello", content_type="text/plain")
    assert doc.declared_id is None
    assert doc.source_uri is None
    assert doc.metadata == {}


def test_decision_record_roundtrip():
    rec = DecisionRecord(
        id="d1", collection="kb", outcome=Outcome.NEW,
        content_hash="abc", principal_id="p1", signals={"sim": 0.1},
    )
    assert rec.outcome is Outcome.NEW
    assert rec.resulting_version_id is None
    assert DecisionRecord.model_validate(rec.model_dump()).id == "d1"
