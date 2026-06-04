from __future__ import annotations
from llmwiki.auth.base import Principal, AuthError


def authorize(principal: Principal, collection: str, action: str) -> None:
    if "admin" in principal.roles:
        return
    if action not in principal.roles:
        raise AuthError(f"principal {principal.id} lacks role '{action}'")
    if collection not in principal.allowed_collections:
        raise AuthError(f"principal {principal.id} not allowed in collection '{collection}'")
