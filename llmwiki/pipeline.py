from __future__ import annotations
import uuid
from llmwiki.config import CollectionConfig, default_pipeline
from llmwiki.models import (IncomingDocument, Document, DocumentVersion,
                            DecisionRecord, ReviewItem, Outcome)
from llmwiki.providers.base import Provider
from llmwiki.rules.base import EvalContext
from llmwiki.rules.engine import build_pipeline, evaluate_pipeline
from llmwiki.storage import IndexStore, ContentStore
from llmwiki.text import content_hash, shingles


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _wiki_page(doc_id: str, text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else doc_id
    return f"# {first[:80]}\n\n{text.strip()}\n"


_TERMINAL_OUTCOME = {
    "ACCEPT": Outcome.NEW, "UPDATE": Outcome.UPDATE, "REPLACE": Outcome.REPLACE,
    "DUPLICATE": Outcome.DUPLICATE, "REJECT": Outcome.REJECTED, "REVIEW": Outcome.NEEDS_REVIEW,
}


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

    def _build_context(self, doc: IncomingDocument, cfg: CollectionConfig) -> EvalContext:
        identity = doc.declared_id or doc.source_uri

        def loader():
            cands = self.index.current_candidates(doc.collection)
            if identity:
                existing = self.index.find_by_identity(doc.collection, identity)
                for c in cands:
                    if existing and c.document_id == existing.id:
                        c.identity_match = True
            return cands

        return EvalContext(doc=doc, provider=self.provider, config=cfg, candidates_loader=loader)

    def ingest(self, doc: IncomingDocument, principal_id: str | None = None) -> DecisionRecord:
        if doc.idempotency_key:
            prior = self.index.get_decision_by_idempotency(doc.collection, doc.idempotency_key)
            if prior is not None:
                return prior

        cfg = self._load_config(doc.collection)
        pipe = build_pipeline(cfg.pipeline or default_pipeline(cfg))
        ctx = self._build_context(doc, cfg)
        decision = evaluate_pipeline(pipe, ctx)

        outcome = _TERMINAL_OUTCOME[decision.disposition]
        rec = DecisionRecord(id=_id("dec"), collection=doc.collection, outcome=outcome,
                             content_hash=ctx.content_hash, principal_id=principal_id,
                             document_id=decision.document_id, reason=decision.reason,
                             signals=decision.signals)

        if outcome in (Outcome.NEW, Outcome.UPDATE, Outcome.REPLACE):
            return self._apply(doc, outcome, rec, ctx, principal_id)
        if outcome is Outcome.NEEDS_REVIEW:
            self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
            self.index.save_review(ReviewItem(
                id=_id("rev"), decision_id=rec.id, collection=doc.collection,
                candidates=[{"document_id": decision.document_id, **decision.signals}]))
            return rec
        self.index.save_decision(rec, idempotency_key=doc.idempotency_key)  # DUPLICATE / REJECTED
        return rec

    def _apply(self, doc, outcome, rec, ctx, principal_id):
        cs = ContentStore(self.content_root, doc.collection)
        identity = ctx.identity
        chash = ctx.content_hash
        if outcome in (Outcome.NEW, Outcome.REPLACE):
            doc_id = _id("doc")
            replaces = rec.document_id if outcome is Outcome.REPLACE else None
            self.index.save_document(Document(id=doc_id, collection=doc.collection,
                                              stable_identity=identity or chash,
                                              wiki_path=f"wiki/{doc_id}.md", replaces=replaces))
            if outcome is Outcome.REPLACE and replaces:
                self.index.mark_replaced(replaces, doc_id)
        else:  # UPDATE
            doc_id = rec.document_id

        version_id = _id("ver")
        commit = cs.write_document(doc_id=doc_id, source_text=doc.content,
                                   wiki_text=_wiki_page(doc_id, doc.content),
                                   log_line=f"{outcome.value} {doc_id} {chash[:8]}")
        self.index.save_version(DocumentVersion(id=version_id, document_id=doc_id,
                                                content_hash=chash, git_commit=commit,
                                                submitter_id=principal_id),
                                embedding=ctx.embedding, shingles=ctx.shingles, content=doc.content)
        self.index.set_current_version(doc_id, version_id)
        rec.document_id = doc_id
        rec.resulting_version_id = version_id
        self.index.save_decision(rec, idempotency_key=doc.idempotency_key)
        return rec
