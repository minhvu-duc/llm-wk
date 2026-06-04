import json
import types
import llmwiki.providers.litellm_provider as mod
from llmwiki.providers.base import AdjudicatorVerdict


def test_embed_calls_litellm(monkeypatch):
    captured = {}

    def fake_embedding(model, input):
        captured["model"] = model
        return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    monkeypatch.setattr(mod, "litellm", types.SimpleNamespace(
        embedding=fake_embedding, completion=None))
    p = mod.LiteLLMProvider(embed_model="m-embed")
    assert p.embed("hi") == [0.1, 0.2, 0.3]
    assert captured["model"] == "m-embed"


def test_adjudicate_parses_json(monkeypatch):
    payload = json.dumps({"relationship": "SAME_UPDATED", "confidence": "HIGH",
                          "rationale": "same topic"})

    def fake_completion(model, messages, **kw):
        return {"choices": [{"message": {"content": payload}}]}

    monkeypatch.setattr(mod, "litellm", types.SimpleNamespace(
        embedding=None, completion=fake_completion))
    p = mod.LiteLLMProvider(adjudicate_model="m-chat")
    v = p.adjudicate("a", "b")
    assert isinstance(v, AdjudicatorVerdict)
    assert v.relationship == "SAME_UPDATED" and v.confidence == "HIGH"


def test_adjudicate_malformed_falls_back_to_review(monkeypatch):
    def fake_completion(model, messages, **kw):
        return {"choices": [{"message": {"content": "not json at all"}}]}

    monkeypatch.setattr(mod, "litellm", types.SimpleNamespace(
        embedding=None, completion=fake_completion))
    p = mod.LiteLLMProvider()
    v = p.adjudicate("a", "b")
    # malformed model output must not become a confident UPDATE
    assert v.confidence == "LOW"
