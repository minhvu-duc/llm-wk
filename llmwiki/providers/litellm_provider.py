from __future__ import annotations
import json
import litellm
from llmwiki.providers.base import AdjudicatorVerdict

_PROMPT = (
    "You compare an INCOMING document against an EXISTING stored document and decide "
    "their relationship. Respond with ONLY a JSON object: "
    '{"relationship": one of "SAME_UPDATED"|"DIFFERENT"|"RELATED_BUT_DISTINCT"|"CONFLICTING", '
    '"confidence": one of "HIGH"|"MEDIUM"|"LOW", "rationale": short string}. '
    "SAME_UPDATED = same logical document, possibly revised. "
    "DIFFERENT = unrelated. RELATED_BUT_DISTINCT = related topic, distinct document. "
    "CONFLICTING = same topic but contradictory facts."
)


class LiteLLMProvider:
    def __init__(self, embed_model: str = "text-embedding-3-small",
                 adjudicate_model: str = "gpt-4o-mini"):
        self.embed_model = embed_model
        self.adjudicate_model = adjudicate_model

    def embed(self, text: str) -> list[float]:
        resp = litellm.embedding(model=self.embed_model, input=text)
        return list(resp["data"][0]["embedding"])

    def adjudicate(self, incoming: str, existing: str) -> AdjudicatorVerdict:
        messages = [
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": f"INCOMING:\n{incoming[:4000]}\n\nEXISTING:\n{existing[:4000]}"},
        ]
        resp = litellm.completion(model=self.adjudicate_model, messages=messages,
                                  temperature=0)
        content = resp["choices"][0]["message"]["content"]
        try:
            data = json.loads(content)
            return AdjudicatorVerdict(**data)
        except Exception:
            # fail safe: never let a malformed response become a confident decision
            return AdjudicatorVerdict(relationship="RELATED_BUT_DISTINCT",
                                      confidence="LOW", rationale="unparseable model output")
