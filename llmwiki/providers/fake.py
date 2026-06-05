from __future__ import annotations
import hashlib
import math
from llmwiki.providers.base import AdjudicatorVerdict, KnowledgeVerdict
from llmwiki.text import normalize

_DIM = 64
_GREETINGS = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "got it", "bye", "yes", "no"}


class FakeProvider:
    """Deterministic provider for tests/dev. Embedding is a hashed bag-of-words
    vector so identical text -> identical vector and overlap -> higher cosine."""

    def __init__(self, verdicts: list[AdjudicatorVerdict] | None = None,
                 assess_verdicts: list[KnowledgeVerdict] | None = None):
        self._verdicts = list(verdicts or [])
        self._assess_verdicts = list(assess_verdicts or [])

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

    def assess(self, text: str, rubric: str) -> KnowledgeVerdict:
        if self._assess_verdicts:
            return self._assess_verdicts.pop(0)
        t = normalize(text).lower()
        words = t.split()
        if len(words) < 5 or t in _GREETINGS:
            return KnowledgeVerdict(is_knowledge=False, category="chitchat",
                                    confidence="HIGH", rationale="too short / greeting")
        return KnowledgeVerdict(is_knowledge=True, category="fact",
                                confidence="HIGH", rationale="substantive")
