from __future__ import annotations
from llmwiki.auth.base import Principal, AuthError


class ApiKeyAuthenticator:
    def __init__(self, keys: dict[str, Principal]):
        self._keys = keys

    def authenticate(self, headers: dict[str, str]) -> Principal:
        raw = headers.get("authorization") or headers.get("Authorization") or ""
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()
        if not token:
            raise AuthError("missing bearer token")
        principal = self._keys.get(token)
        if principal is None:
            raise AuthError("unknown api key")
        return principal
