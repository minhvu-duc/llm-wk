from __future__ import annotations
from fastapi import FastAPI
from llmwiki.api.routers import build_router
from llmwiki.auth.base import Authenticator
from llmwiki.coordinator import Coordinator
from llmwiki.pipeline import IngestService
from llmwiki.providers.fake import FakeProvider
from llmwiki.storage import IndexStore


def _make_provider(name: str):
    if name == "fake":
        return FakeProvider()
    from llmwiki.providers.litellm_provider import LiteLLMProvider
    return LiteLLMProvider()


def create_app(data_dir: str, authenticator: Authenticator | None = None,
               provider_name: str = "fake", authenticator_factory=None) -> FastAPI:
    provider = _make_provider(provider_name)
    index = IndexStore(f"{data_dir}/index.db")
    service = IngestService(index=index, content_root=f"{data_dir}/repos", provider=provider)
    coordinator = Coordinator(service)
    app = FastAPI(title="llmwiki")
    app.state.index = index
    app.state.service = service
    app.state.coordinator = coordinator
    app.state.provider = provider
    # authenticator_factory(index) lets stored/db-backed authenticators bind to this index
    app.state.authenticator = authenticator_factory(index) if authenticator_factory else authenticator

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    app.include_router(build_router())
    return app
