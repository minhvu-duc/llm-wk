"""Embedded usage — use llmwiki as a library, no server needed.

Run:  .venv/bin/python examples/quickstart_library.py
"""
import tempfile

from llmwiki.storage import IndexStore
from llmwiki.pipeline import IngestService
from llmwiki.coordinator import Coordinator
from llmwiki.providers.fake import FakeProvider          # swap for LiteLLMProvider() in prod
from llmwiki.models import IncomingDocument, Outcome

# 1. Wire up the engine once at startup.
data_dir = tempfile.mkdtemp()
service = IngestService(
    index=IndexStore(f"{data_dir}/index.db"),
    content_root=f"{data_dir}/repos",
    provider=FakeProvider(),        # offline & deterministic; use LiteLLMProvider() for real embeddings
)
engine = Coordinator(service)       # serializes ingests per collection (correct concurrent decisions)
service.ensure_collection("handbook")


def submit(content: str, **kw) -> "DecisionRecord":
    """Hand a document to the engine and let it decide what to do."""
    doc = IncomingDocument(collection="handbook", content=content, **kw)
    rec = engine.ingest(doc, principal_id="etl-job")     # principal_id = who submitted it
    print(f"{rec.outcome.value:13} | {rec.reason:35} | doc={rec.document_id}")
    return rec


# 2. Feed documents. The engine decides NEW / DUPLICATE / UPDATE / NEEDS_REVIEW for each.
submit("Vacation policy: 20 days per year.", declared_id="policy/vacation")   # first time
submit("Vacation policy: 20 days per year.", declared_id="policy/vacation")   # exact resend
submit("Vacation policy: 25 days per year.", declared_id="policy/vacation")   # revised
submit("Expense policy: submit receipts within 30 days.", declared_id="policy/expense")

# 3. Branch on the outcome inside your own workflow.
rec = submit("Vacation policy: 25 days per year, plus 5 floating holidays.",
             declared_id="policy/vacation")
if rec.outcome is Outcome.NEEDS_REVIEW:
    print("   -> ambiguous: queued for a human, NOT auto-applied")
elif rec.outcome in (Outcome.NEW, Outcome.UPDATE):
    print(f"   -> stored as version {rec.resulting_version_id} (committed to git wiki)")
elif rec.outcome is Outcome.DUPLICATE:
    print("   -> already had it, nothing to do")

print(f"\nWiki + git history live under: {data_dir}/repos/handbook")
