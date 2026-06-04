from __future__ import annotations
from typing import Protocol
from pydantic import BaseModel, Field


class AuthError(Exception):
    """Raised on authentication or authorization failure."""


class Principal(BaseModel):
    id: str
    allowed_collections: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


class Authenticator(Protocol):
    def authenticate(self, headers: dict[str, str]) -> Principal: ...
