from __future__ import annotations
import threading
from collections import defaultdict
from llmwiki.models import IncomingDocument, DecisionRecord
from llmwiki.pipeline import IngestService


class Coordinator:
    """Serializes ingests per collection so the decision always sees consistent
    state. Different collections proceed in parallel."""

    def __init__(self, service: IngestService):
        self._svc = service
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    def _lock_for(self, collection: str) -> threading.Lock:
        with self._guard:
            return self._locks[collection]

    def ingest(self, doc: IncomingDocument, principal_id: str | None = None) -> DecisionRecord:
        with self._lock_for(doc.collection):
            return self._svc.ingest(doc, principal_id=principal_id)
