from __future__ import annotations
import hashlib
import re
import unicodedata

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    return _WS.sub(" ", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def shingles(text: str, n: int = 3) -> set[str]:
    tokens = normalize(text).lower().split()
    if len(tokens) < n:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
