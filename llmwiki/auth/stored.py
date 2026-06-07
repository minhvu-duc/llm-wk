from __future__ import annotations
import hashlib
import secrets
from llmwiki.auth.base import Principal, AuthError


def generate_key() -> str:
    """A fresh API key secret, shown to the operator once."""
    return "lw_" + secrets.token_hex(16)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class StoredAuthenticator:
    """Authenticates bearer tokens against API keys persisted in the index store."""

    def __init__(self, index):
        self._index = index

    def authenticate(self, headers: dict[str, str]) -> Principal:
        raw = headers.get("authorization") or headers.get("Authorization") or ""
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()
        if not token:
            raise AuthError("missing bearer token")
        record = self._index.get_api_key_by_hash(hash_key(token))
        if record is None:
            raise AuthError("unknown or revoked api key")
        return Principal(id=record["id"], allowed_collections=record["allowed_collections"],
                         roles=record["roles"])
