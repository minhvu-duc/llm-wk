"""Service usage — call llmwiki over HTTP from any project or language.

First start the server in another terminal:
    LLMWIKI_API_KEY=dev-key .venv/bin/llmwiki serve

Then run:
    .venv/bin/python examples/quickstart_http.py

(Uses httpx, which ships with the dev deps; `requests` works identically.)
"""
import httpx

BASE = "http://localhost:8000"
HEADERS = {"Authorization": "Bearer dev-key"}   # the dev key maps to an admin principal


def ensure_collection(name: str) -> None:
    httpx.post(f"{BASE}/v1/collections", json={"name": name}, headers=HEADERS)


def ingest(collection: str, content: str, declared_id: str | None = None,
           idempotency_key: str | None = None) -> dict:
    body = {"content": content, "declared_id": declared_id, "idempotency_key": idempotency_key}
    r = httpx.post(f"{BASE}/v1/collections/{collection}/documents", json=body, headers=HEADERS)
    r.raise_for_status()
    return r.json()                # -> {"outcome": "...", "document_id": "...", "reason": "...", ...}


def pending_reviews(collection: str) -> list[dict]:
    return httpx.get(f"{BASE}/v1/collections/{collection}/reviews", headers=HEADERS).json()


def resolve(review_id: str, resolution: str) -> None:
    # resolution: "as_update" | "as_new" | "reject"
    httpx.post(f"{BASE}/v1/reviews/{review_id}/resolve",
               json={"resolution": resolution}, headers=HEADERS)


if __name__ == "__main__":
    ensure_collection("handbook")

    d = ingest("handbook", "Security policy: rotate keys every 90 days.",
               declared_id="policy/security")
    print(d["outcome"], "->", d["document_id"])          # NEW

    d = ingest("handbook", "Security policy: rotate keys every 90 days.",
               declared_id="policy/security")
    print(d["outcome"])                                  # DUPLICATE

    # Process anything the engine wasn't confident about.
    for item in pending_reviews("handbook"):
        print("needs review:", item["id"], item["candidates"])
        resolve(item["id"], "as_update")
