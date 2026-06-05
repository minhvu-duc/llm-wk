from __future__ import annotations
import re
from fastapi import APIRouter, Request, HTTPException
from llmwiki.api.schemas import IngestRequest, CreateCollectionRequest, ResolveRequest
from llmwiki.auth.base import AuthError
from llmwiki.auth.authz import authorize
from llmwiki.config import CollectionConfig
from llmwiki.models import IncomingDocument


def _validate_and_dump_config(config: dict) -> dict:
    try:
        cfg = CollectionConfig(**config)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid config: {e}")
    for pattern in cfg.denylist_patterns:
        try:
            re.compile(pattern)
        except re.error as e:
            raise HTTPException(status_code=422, detail=f"invalid denylist regex /{pattern}/: {e}")
    return cfg.model_dump()


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
        principal = _principal(request)
        # any authenticated principal may create; scope enforced on ingest
        request.app.state.service.ensure_collection(body.name)
        if body.config is not None:
            _check(principal, body.name, "admin")
            request.app.state.index.set_collection_config(
                body.name, _validate_and_dump_config(body.config))
        return {"name": body.name}

    @r.get("/collections/{collection}/config")
    def get_config(collection: str, request: Request):
        principal = _principal(request)
        _check(principal, collection, "read")
        row = request.app.state.index.get_collection(collection)
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        return CollectionConfig(**row["config"]).model_dump() if row["config"] \
            else CollectionConfig().model_dump()

    @r.put("/collections/{collection}/config")
    def put_config(collection: str, body: dict, request: Request):
        principal = _principal(request)
        _check(principal, collection, "admin")
        if request.app.state.index.get_collection(collection) is None:
            raise HTTPException(status_code=404, detail="not found")
        dumped = _validate_and_dump_config(body)
        request.app.state.index.set_collection_config(collection, dumped)
        return dumped

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
