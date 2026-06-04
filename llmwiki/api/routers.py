from __future__ import annotations
from fastapi import APIRouter, Request, HTTPException
from llmwiki.api.schemas import IngestRequest, CreateCollectionRequest, ResolveRequest
from llmwiki.auth.base import AuthError
from llmwiki.auth.authz import authorize
from llmwiki.models import IncomingDocument


def _principal(request: Request):
    headers = {k: v for k, v in request.headers.items()}
    try:
        return request.app.state.authenticator.authenticate(headers)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))


def _check(principal, collection, action):
    try:
        authorize(principal, collection, action)
    except AuthError as e:
        raise HTTPException(status_code=403, detail=str(e))


def build_router() -> APIRouter:
    r = APIRouter(prefix="/v1")

    @r.post("/collections")
    def create_collection(body: CreateCollectionRequest, request: Request):
        _principal(request)
        # any authenticated principal may create; scope enforced on ingest
        request.app.state.service.ensure_collection(body.name)
        return {"name": body.name}

    @r.post("/collections/{collection}/documents")
    def ingest(collection: str, body: IngestRequest, request: Request):
        principal = _principal(request)
        _check(principal, collection, "ingest")
        doc = IncomingDocument(collection=collection, **body.model_dump())
        rec = request.app.state.coordinator.ingest(doc, principal_id=principal.id)
        return rec.model_dump(mode="json")

    @r.get("/collections/{collection}/documents/{doc_id}")
    def get_document(collection: str, doc_id: str, request: Request):
        principal = _principal(request)
        _check(principal, collection, "read")
        doc = request.app.state.index.get_document(doc_id)
        if not doc or doc.collection != collection:
            raise HTTPException(status_code=404, detail="not found")
        return doc.model_dump(mode="json")

    @r.get("/decisions/{decision_id}")
    def get_decision(decision_id: str, request: Request):
        _principal(request)
        rec = request.app.state.index.get_decision(decision_id)
        if not rec:
            raise HTTPException(status_code=404, detail="not found")
        return rec.model_dump(mode="json")

    @r.get("/collections/{collection}/reviews")
    def list_reviews(collection: str, request: Request):
        principal = _principal(request)
        _check(principal, collection, "reviewer")
        return [item.model_dump(mode="json")
                for item in request.app.state.index.list_reviews(collection)]

    @r.post("/reviews/{review_id}/resolve")
    def resolve(review_id: str, body: ResolveRequest, request: Request):
        principal = _principal(request)
        item = request.app.state.index.get_review(review_id)
        if not item:
            raise HTTPException(status_code=404, detail="not found")
        _check(principal, item.collection, "reviewer")
        item.status = "resolved"
        item.resolution = body.resolution
        item.resolver_id = principal.id
        request.app.state.index.save_review(item)
        return item.model_dump(mode="json")

    return r
