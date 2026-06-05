from __future__ import annotations
import uuid
from llmwiki.classifier import classify, Fingerprint
from llmwiki.config import CollectionConfig
from llmwiki.models import (IncomingDocument, Document, DocumentVersion,
                            DecisionRecord, ReviewItem, Outcome)
from llmwiki.providers.base import Provider
from llmwiki.gates.chain import build_chain
from llmwiki.gates.base import GateContext
from llmwiki.storage import IndexStore, ContentStore
from llmwiki.text import content_hash, shingles


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _wiki_page(doc_id: str, text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else doc_id
    return f"# {first[:80]}\n\n{text.strip()}\n"


class IngestService:
    def __init__(self, index: IndexStore, content_root: str, provider: Provider,
                 config: CollectionConfig | None = None):
        self.index = index
        self.content_root = content_root
        self.provider = provider
        self.config = config or CollectionConfig()

    def ensure_collection(self, name: str) -> None:
        self.index.create_collection(name)
        ContentStore(self.content_root, name).init()

    def _load_config(self, collection: str) -> CollectionConfig:
        row = self.index.get_collection(collection)
        stored = row.get("config") if row else None
        return CollectionConfig(**stored) if stored else self.config

    def ingest(self, doc: IncomingDocument, principal_id: str | None = None) -> DecisionRecord:
        # idempotency short-circuit
        if doc.idempotency_key:
            prior = self.index.get_decision_by_idempotency(doc.collection, doc.idempotency_key)
            if prior is not None:
                return prior

        cfg = self._load_config(doc.collection)

        # quality gates run BEFORE embedding so rejected content costs nothing downstream
        if cfg.quality_enabled:
            verdict = build_chain(cfg).run(doc, GateContext(config=cfg, provider=self.provider))
            if verdict is not None and verdict.decision != "PASS":
                return self._record_gate_block(doc, verdict, principal_id)

        chash = content_hash(doc.content)
        embedding = self.provider.embed(doc.content)
        sh = shingles(doc.content)
        identity = doc.declared_id or doc.source_uri

        candidates = self.index.current_candidates(doc.collection)
        if identity:
            existing = self.index.find_by_identity(doc.collection, identity)
            for c in candidates:
                if existing and c.document_id == existing.id:
                    c.identity_match = True

        fp = Fingerprint(content_hash=chash, embedding=embedding, shingles=sh,
                         declared_id=identity)
        decision = classify(fp, candidates, cfg, self.provider.adjudicate)

        rec = DecisionRecord(id=_id("dec"), collection=doc.collection, outcome=decision.outcome,
                             content_hash=chash, principal_id=principal_id,
                             document_id=decision.document_id, reason=decision.reason,
                             signals=decision.signals)

        if decision.outcome in (Outcome.NEW, Outcome.UPDATE):
            rec = self._apply(doc, decision, rec, chash, embedding, sh, identity, principal_id)
        elif decision.outcome is Outcome.NEEDS_REVIEW:
            self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
            self.index.save_review(ReviewItem(
                id=_id("rev"), decision_id=rec.id, collection=doc.collection,
                candidates=[{"document_id": decision.document_id, **decision.signals}]))
            return rec
        else:  # DUPLICATE / REJECTED
            self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
        return rec

    def _record_gate_block(self, doc: IncomingDocument, verdict, principal_id) -> DecisionRecord:
        outcome = Outcome.REJECTED if verdict.decision == "REJECT" else Outcome.NEEDS_REVIEW
        rec = DecisionRecord(id=_id("dec"), collection=doc.collection, outcome=outcome,
                             content_hash=content_hash(doc.content), principal_id=principal_id,
                             reason=verdict.reason,
                             signals={"gate": verdict.gate, **verdict.signals})
        self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
        if outcome is Outcome.NEEDS_REVIEW:
            self.index.save_review(ReviewItem(
                id=_id("rev"), decision_id=rec.id, collection=doc.collection,
                candidates=[{"gate": verdict.gate, "reason": verdict.reason, **verdict.signals}]))
        return rec

    def _apply(self, doc, decision, rec, chash, embedding, sh, identity, principal_id):
        cs = ContentStore(self.content_root, doc.collection)
        if decision.outcome is Outcome.NEW:
            doc_id = _id("doc")
            self.index.save_document(Document(id=doc_id, collection=doc.collection,
                                              stable_identity=identity or chash,
                                              wiki_path=f"wiki/{doc_id}.md"))
        else:  # UPDATE
            doc_id = decision.document_id

        version_id = _id("ver")
        commit = cs.write_document(doc_id=doc_id, source_text=doc.content,
                                   wiki_text=_wiki_page(doc_id, doc.content),
                                   log_line=f"{decision.outcome.value} {doc_id} {chash[:8]}")
        self.index.save_version(DocumentVersion(id=version_id, document_id=doc_id,
                                                content_hash=chash, git_commit=commit,
                                                submitter_id=principal_id),
                                embedding=embedding, shingles=sh, content=doc.content)
        self.index.set_current_version(doc_id, version_id)
        rec.document_id = doc_id
        rec.resulting_version_id = version_id
        self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
        return rec
