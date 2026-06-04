from __future__ import annotations
import hashlib
import math
from llmwiki.providers.base import AdjudicatorVerdict
from llmwiki.text import normalize

_DIM = 64


class FakeProvider:
    """Deterministic provider for tests/dev. Embedding is a hashed bag-of-words
    vector so identical text -> identical vector and overlap -> higher cosine."""

    def __init__(self, verdicts: list[AdjudicatorVerdict] | None = None):
        self._verdicts = list(verdicts or [])

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * _DIM
        for tok in normalize(text).lower().split():
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % _DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def adjudicate(self, incoming: str, existing: str) -> AdjudicatorVerdict:
        if self._verdicts:
            return self._verdicts.pop(0)
        return AdjudicatorVerdict(relationship="DIFFERENT", confidence="HIGH")
