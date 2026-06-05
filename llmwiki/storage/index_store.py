from __future__ import annotations
import json
import os
import sqlite3
from llmwiki.classifier import Candidate
from llmwiki.models import Document, DocumentVersion, DecisionRecord, ReviewItem, Outcome

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
  name TEXT PRIMARY KEY, config TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, collection TEXT NOT NULL, stable_identity TEXT NOT NULL,
  current_version_id TEXT, wiki_path TEXT, created_at TEXT, updated_at TEXT,
  UNIQUE(collection, stable_identity));
CREATE TABLE IF NOT EXISTS versions (
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL, content_hash TEXT NOT NULL,
  git_commit TEXT, submitter_id TEXT, created_at TEXT,
  embedding TEXT NOT NULL DEFAULT '[]', shingles TEXT NOT NULL DEFAULT '[]',
  content TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, collection TEXT NOT NULL, outcome TEXT NOT NULL,
  content_hash TEXT, principal_id TEXT, document_id TEXT, resulting_version_id TEXT,
  reason TEXT, signals TEXT, created_at TEXT, idempotency_key TEXT);
CREATE INDEX IF NOT EXISTS idx_decisions_idem ON decisions(collection, idempotency_key);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, collection TEXT NOT NULL,
  status TEXT NOT NULL, candidates TEXT, resolution TEXT, resolver_id TEXT, created_at TEXT);
"""


class IndexStore:
    def __init__(self, path: str):
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --- collections ---
    def create_collection(self, name: str, config: dict | None = None) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO collections(name, config) VALUES (?, ?)",
            (name, json.dumps(config or {})))
        self._conn.commit()

    def get_collection(self, name: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM collections WHERE name=?", (name,)).fetchone()
        return {"name": row["name"], "config": json.loads(row["config"])} if row else None

    def set_collection_config(self, name: str, config: dict) -> None:
        self._conn.execute(
            "INSERT INTO collections(name, config) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET config=excluded.config",
            (name, json.dumps(config)))
        self._conn.commit()

    # --- documents / versions ---
    def save_document(self, doc: Document) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO documents
               (id, collection, stable_identity, current_version_id, wiki_path, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (doc.id, doc.collection, doc.stable_identity, doc.current_version_id,
             doc.wiki_path, doc.created_at.isoformat(), doc.updated_at.isoformat()))
        self._conn.commit()

    def get_document(self, doc_id: str) -> Document | None:
        row = self._conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return self._row_to_doc(row) if row else None

    def find_by_identity(self, collection: str, identity: str) -> Document | None:
        row = self._conn.execute(
            "SELECT * FROM documents WHERE collection=? AND stable_identity=?",
            (collection, identity)).fetchone()
        return self._row_to_doc(row) if row else None

    def set_current_version(self, doc_id: str, version_id: str) -> None:
        self._conn.execute(
            "UPDATE documents SET current_version_id=? WHERE id=?", (version_id, doc_id))
        self._conn.commit()

    def save_version(self, v: DocumentVersion, embedding: list[float],
                     shingles: set[str], content: str = "") -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO versions
               (id, document_id, content_hash, git_commit, submitter_id, created_at,
                embedding, shingles, content) VALUES (?,?,?,?,?,?,?,?,?)""",
            (v.id, v.document_id, v.content_hash, v.git_commit, v.submitter_id,
             v.created_at.isoformat(), json.dumps(embedding), json.dumps(sorted(shingles)), content))
        self._conn.commit()

    def current_candidates(self, collection: str) -> list[Candidate]:
        rows = self._conn.execute(
            """SELECT d.id AS doc_id, v.content_hash, v.embedding, v.shingles, v.content
               FROM documents d JOIN versions v ON d.current_version_id = v.id
               WHERE d.collection=?""", (collection,)).fetchall()
        return [Candidate(document_id=r["doc_id"], content_hash=r["content_hash"],
                          embedding=json.loads(r["embedding"]),
                          shingles=set(json.loads(r["shingles"])), content=r["content"])
                for r in rows]

    # --- decisions / reviews ---
    def save_decision(self, rec: DecisionRecord, idempotency_key: str | None = None) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO decisions
               (id, collection, outcome, content_hash, principal_id, document_id,
                resulting_version_id, reason, signals, created_at, idempotency_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (rec.id, rec.collection, rec.outcome.value, rec.content_hash, rec.principal_id,
             rec.document_id, rec.resulting_version_id, rec.reason, json.dumps(rec.signals),
             rec.created_at.isoformat(), idempotency_key))
        self._conn.commit()

    def get_decision(self, decision_id: str) -> DecisionRecord | None:
        row = self._conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
        return self._row_to_decision(row) if row else None

    def get_decision_by_idempotency(self, collection: str, key: str) -> DecisionRecord | None:
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE collection=? AND idempotency_key=?",
            (collection, key)).fetchone()
        return self._row_to_decision(row) if row else None

    def save_review(self, item: ReviewItem) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO reviews
               (id, decision_id, collection, status, candidates, resolution, resolver_id, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (item.id, item.decision_id, item.collection, item.status,
             json.dumps(item.candidates), item.resolution, item.resolver_id,
             item.created_at.isoformat()))
        self._conn.commit()

    def list_reviews(self, collection: str, status: str = "pending") -> list[ReviewItem]:
        rows = self._conn.execute(
            "SELECT * FROM reviews WHERE collection=? AND status=?", (collection, status)).fetchall()
        return [self._row_to_review(r) for r in rows]

    def get_review(self, review_id: str) -> ReviewItem | None:
        row = self._conn.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
        return self._row_to_review(row) if row else None

    # --- helpers ---
    def _row_to_doc(self, row) -> Document:
        from datetime import datetime
        return Document(id=row["id"], collection=row["collection"],
                        stable_identity=row["stable_identity"],
                        current_version_id=row["current_version_id"], wiki_path=row["wiki_path"],
                        created_at=datetime.fromisoformat(row["created_at"]),
                        updated_at=datetime.fromisoformat(row["updated_at"]))

    def _row_to_decision(self, row) -> DecisionRecord:
        from datetime import datetime
        return DecisionRecord(id=row["id"], collection=row["collection"],
                              outcome=Outcome(row["outcome"]), content_hash=row["content_hash"],
                              principal_id=row["principal_id"], document_id=row["document_id"],
                              resulting_version_id=row["resulting_version_id"], reason=row["reason"] or "",
                              signals=json.loads(row["signals"] or "{}"),
                              created_at=datetime.fromisoformat(row["created_at"]))

    def _row_to_review(self, row) -> ReviewItem:
        return ReviewItem(id=row["id"], decision_id=row["decision_id"], collection=row["collection"],
                          status=row["status"], candidates=json.loads(row["candidates"] or "[]"),
                          resolution=row["resolution"], resolver_id=row["resolver_id"])
