from __future__ import annotations
import argparse
import os
from llmwiki.auth.apikey import ApiKeyAuthenticator
from llmwiki.auth.base import Principal


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="llmwiki")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--data-dir", default=os.environ.get("LLMWIKI_DATA", "./data"))
    i = sub.add_parser("init-collection")
    i.add_argument("name")
    i.add_argument("--data-dir", default=os.environ.get("LLMWIKI_DATA", "./data"))
    return p


def _dev_authenticator() -> ApiKeyAuthenticator:
    key = os.environ.get("LLMWIKI_API_KEY", "dev-key")
    return ApiKeyAuthenticator({key: Principal(id="dev", allowed_collections=[], roles=["admin"])})


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-collection":
        from llmwiki.storage import IndexStore, ContentStore
        IndexStore(f"{args.data_dir}/index.db").create_collection(args.name)
        ContentStore(f"{args.data_dir}/repos", args.name).init()
        print(f"initialized collection {args.name}")
        return 0
    if args.command == "serve":  # pragma: no cover
        import uvicorn
        from llmwiki.api.app import create_app
        from llmwiki.dashboard import attach_dashboard
        app = create_app(data_dir=args.data_dir, authenticator=_dev_authenticator(),
                         provider_name=os.environ.get("LLMWIKI_PROVIDER", "fake"))
        attach_dashboard(app)
        uvicorn.run(app, host=args.host, port=args.port)
        return 0
    return 1
