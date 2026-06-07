from __future__ import annotations
import re
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from llmwiki.api.schemas import (IngestRequest, CreateCollectionRequest, ResolveRequest,
                                 CreateKeyRequest, QueryRequest)
from llmwiki.auth.base import AuthError
from llmwiki.auth.authz import authorize
from llmwiki.auth.stored import generate_key, hash_key
from llmwiki.config import CollectionConfig
from llmwiki.models import IncomingDocument
from llmwiki.rules.engine import build_pipeline


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
    if cfg.pipeline is not None:
        try:
            build_pipeline(cfg.pipeline)   # resolves rule types + validates each rule's params
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"invalid pipeline: {e}")
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"invalid pipeline params: {e}")
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


def _require_admin(principal):
    if "admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="admin role required")


def build_router() -> APIRouter:
    r = APIRouter(prefix="/v1")

    @r.post("/keys")
    def create_key(body: CreateKeyRequest, request: Request):
        principal = _principal(request)
        _require_admin(principal)
        raw = generate_key()
        key_id = f"key_{uuid.uuid4().hex[:12]}"
        request.app.state.index.create_api_key(
            id=key_id, key_hash=hash_key(raw), name=body.name,
            allowed_collections=body.allowed_collections, roles=body.roles,
            created_at=datetime.now(timezone.utc).isoformat())
        return {"id": key_id, "name": body.name, "key": raw,
                "allowed_collections": body.allowed_collections, "roles": body.roles}

    @r.get("/keys")
    def list_keys(request: Request):
        _require_admin(_principal(request))
        return request.app.state.index.list_api_keys()

    @r.delete("/keys/{key_id}")
    def revoke_key(key_id: str, request: Request):
        _require_admin(_principal(request))
        if not request.app.state.index.revoke_api_key(key_id):
            raise HTTPException(status_code=404, detail="not found")
        return {"id": key_id, "revoked": True}

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

    @r.post("/collections/{collection}/query")
    def query_zone(collection: str, body: QueryRequest, request: Request):
        principal = _principal(request)
        _check(principal, collection, "query")
        results = request.app.state.query.query_zone(collection, body.query, body.top_k)
        return {"query": body.query, "results": results}

    @r.post("/query")
    def query_global(body: QueryRequest, request: Request):
        principal = _principal(request)
        if "query" not in principal.roles and "admin" not in principal.roles:
            raise HTTPException(status_code=403, detail="query role required")
        q = request.app.state.query
        zones = q.resolve_zones(principal.allowed_collections, body.collections)
        results = q.query_global(zones, body.query, body.top_k)
        return {"query": body.query, "zones": zones, "results": results}

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
