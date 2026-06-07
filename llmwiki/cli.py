from __future__ import annotations
import argparse
import os
from datetime import datetime, timezone
from llmwiki.auth.stored import StoredAuthenticator, hash_key


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


def _seed_admin_key(index, raw_key: str) -> None:
    """Ensure the configured admin key exists in the store (idempotent by hash)."""
    if index.get_api_key_by_hash(hash_key(raw_key)) is None:
        index.create_api_key(id="key_admin", key_hash=hash_key(raw_key), name="bootstrap-admin",
                             allowed_collections=["*"], roles=["admin"],
                             created_at=datetime.now(timezone.utc).isoformat())


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
        from llmwiki.admin_ui import attach_admin
        from llmwiki.storage import IndexStore
        admin_key = os.environ.get("LLMWIKI_API_KEY", "dev-key")
        _seed_admin_key(IndexStore(f"{args.data_dir}/index.db"), admin_key)
        app = create_app(data_dir=args.data_dir, authenticator_factory=StoredAuthenticator,
                         provider_name=os.environ.get("LLMWIKI_PROVIDER", "fake"))
        attach_dashboard(app)
        attach_admin(app)
        print(f"llmwiki serving on {args.host}:{args.port}  (admin key: {admin_key})")
        uvicorn.run(app, host=args.host, port=args.port)
        return 0
    return 1
