from __future__ import annotations
import html
import json
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from llmwiki.auth.base import AuthError
from llmwiki.config import CollectionConfig, default_pipeline
from llmwiki.rules.engine import build_pipeline

_CSS = """
<style>
 body{font:15px/1.5 system-ui,sans-serif;margin:0;color:#1a1a1a;background:#f6f7f9}
 header{background:#1f2937;color:#fff;padding:12px 20px;display:flex;gap:16px;align-items:center}
 header a{color:#cbd5e1;text-decoration:none} header a:hover{color:#fff}
 main{max-width:960px;margin:24px auto;padding:0 16px}
 .card{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin:12px 0}
 .gate{border-left:4px solid #6366f1;padding:8px 12px;margin:8px 0;background:#f9fafb}
 .rule{display:inline-block;background:#eef2ff;border:1px solid #c7d2fe;border-radius:6px;
   padding:2px 8px;margin:3px;font-size:13px}
 .cat{font-size:11px;color:#6b7280;text-transform:uppercase}
 textarea{width:100%;min-height:200px;font:13px monospace;border:1px solid #d1d5db;border-radius:6px;padding:8px}
 button{background:#4f46e5;color:#fff;border:0;border-radius:6px;padding:8px 14px;cursor:pointer}
 button.secondary{background:#6b7280} button.danger{background:#dc2626}
 input{padding:7px;border:1px solid #d1d5db;border-radius:6px}
 table{border-collapse:collapse;width:100%} td,th{border:1px solid #e5e7eb;padding:6px 8px;text-align:left;font-size:13px}
 .err{color:#b91c1c;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:8px;margin:8px 0}
 .ok{color:#065f46;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:6px;padding:8px;margin:8px 0}
 code{background:#f3f4f6;padding:1px 5px;border-radius:4px}
</style>
"""


def _page(title: str, body: str) -> HTMLResponse:
    nav = ('<header><strong>llmwiki admin</strong>'
           '<a href="/admin">Realms</a><a href="/admin/keys">API keys</a>'
           '<a href="/admin/help">Gate concepts</a>'
           '<a href="/admin/logout" style="margin-left:auto">Log out</a></header>')
    return HTMLResponse(f"<!doctype html><html><head><title>{html.escape(title)}</title>{_CSS}"
                        f"</head><body>{nav}<main>{body}</main></body></html>")


def _principal(request: Request):
    """Resolve the admin principal from the lw_key cookie, or None."""
    key = request.cookies.get("lw_key")
    if not key:
        return None
    try:
        p = request.app.state.authenticator.authenticate({"authorization": f"Bearer {key}"})
    except AuthError:
        return None
    return p if "admin" in p.roles else None


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


def attach_admin(app: FastAPI) -> None:

    @app.get("/admin/login", response_class=HTMLResponse)
    def login_form(error: str = ""):
        msg = f'<div class="err">{html.escape(error)}</div>' if error else ""
        body = (f'<div class="card"><h2>Admin login</h2>{msg}'
                '<form method="post" action="/admin/login">'
                '<p>Paste an admin API key:</p>'
                '<input name="key" type="password" style="width:60%" placeholder="lw_..."/> '
                '<button type="submit">Log in</button></form></div>')
        return _page("login", body)

    @app.post("/admin/login")
    def login(key: str = Form(...), request: Request = None):
        try:
            p = request.app.state.authenticator.authenticate({"authorization": f"Bearer {key}"})
        except AuthError:
            p = None
        if p is None or "admin" not in p.roles:
            return RedirectResponse("/admin/login?error=invalid+admin+key", status_code=303)
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie("lw_key", key, httponly=True, samesite="lax")
        return resp

    @app.get("/admin/logout")
    def logout():
        resp = RedirectResponse("/admin/login", status_code=303)
        resp.delete_cookie("lw_key")
        return resp

    @app.get("/admin", response_class=HTMLResponse)
    def home(request: Request):
        if _principal(request) is None:
            return _login_redirect()
        realms = request.app.state.index.list_collections()
        rows = "".join(f'<li><a href="/admin/realms/{html.escape(c)}">{html.escape(c)}</a></li>'
                       for c in realms) or "<li>(no realms yet)</li>"
        body = (f'<div class="card"><h2>Realms (zones)</h2><ul>{rows}</ul></div>'
                '<div class="card">Each realm is an isolated zone with its own gate pipeline, '
                'documents, and git history. Manage access under '
                '<a href="/admin/keys">API keys</a>.</div>')
        return _page("realms", body)

    @app.get("/admin/realms/{collection}", response_class=HTMLResponse)
    def realm(collection: str, request: Request, error: str = "", ok: str = ""):
        if _principal(request) is None:
            return _login_redirect()
        index = request.app.state.index
        row = index.get_collection(collection)
        if row is None:
            return _page("not found", f'<div class="card">Realm {html.escape(collection)} not found.</div>')
        cfg = CollectionConfig(**row["config"]) if row["config"] else CollectionConfig()
        pipeline = cfg.pipeline or default_pipeline(cfg)

        gate_html = ""
        for gate in pipeline:
            chips = "".join(f'<span class="rule">{html.escape(rl["type"])}</span>'
                            for rl in gate.get("rules", []))
            gate_html += (f'<div class="gate"><strong>{html.escape(gate.get("gate", ""))}</strong>'
                          f'<br>{chips}</div>')

        editor_json = html.escape(json.dumps(pipeline, indent=2))
        banner = (f'<div class="err">{html.escape(error)}</div>' if error else
                  f'<div class="ok">{html.escape(ok)}</div>' if ok else "")

        reviews = index.list_reviews(collection)
        rev_rows = ""
        for r in reviews:
            rev_rows += (
                f'<tr><td>{html.escape(r.id)}</td><td>{html.escape(json.dumps(r.candidates))}</td>'
                f'<td><form method="post" action="/admin/realms/{collection}/reviews/{r.id}/resolve" '
                'style="display:flex;gap:4px">'
                '<button name="resolution" value="as_update">as update</button>'
                '<button name="resolution" value="as_new" class="secondary">as new</button>'
                '<button name="resolution" value="reject" class="danger">reject</button>'
                '</form></td></tr>')
        rev_table = (f'<table><tr><th>review</th><th>candidates</th><th>resolve</th></tr>{rev_rows}</table>'
                     if rev_rows else "<p>No pending reviews.</p>")

        decisions = index.recent_decisions(collection, 15)
        dec_rows = "".join(
            f'<tr><td>{html.escape(d.outcome.value)}</td><td>{html.escape(d.reason)}</td>'
            f'<td>{html.escape(d.created_at.isoformat())}</td></tr>' for d in decisions)
        dec_table = (f'<table><tr><th>outcome</th><th>reason</th><th>when</th></tr>{dec_rows}</table>'
                     if dec_rows else "<p>No decisions yet.</p>")

        body = (
            f'<h2>Realm: {html.escape(collection)}</h2>{banner}'
            f'<div class="card"><h3>Pipeline</h3>{gate_html}</div>'
            f'<div class="card"><h3>Edit pipeline (JSON)</h3>'
            f'<form method="post" action="/admin/realms/{collection}/config">'
            f'<textarea name="pipeline_json">{editor_json}</textarea><br>'
            '<button type="submit">Save pipeline</button></form></div>'
            f'<div class="card"><h3>Review queue</h3>{rev_table}</div>'
            f'<div class="card"><h3>Recent decisions</h3>{dec_table}</div>')
        return _page(f"realm {collection}", body)

    @app.post("/admin/realms/{collection}/config")
    def save_pipeline(collection: str, pipeline_json: str = Form(...), request: Request = None):
        if _principal(request) is None:
            return _login_redirect()
        try:
            pipeline = json.loads(pipeline_json)
            cfg = CollectionConfig(pipeline=pipeline)
            build_pipeline(pipeline)  # validates rule types + params
        except Exception as e:
            return RedirectResponse(f"/admin/realms/{collection}?error={html.escape(str(e))[:200]}",
                                    status_code=303)
        request.app.state.index.set_collection_config(collection, cfg.model_dump())
        return RedirectResponse(f"/admin/realms/{collection}?ok=pipeline+saved", status_code=303)

    @app.post("/admin/realms/{collection}/reviews/{review_id}/resolve")
    def resolve_review(collection: str, review_id: str, resolution: str = Form(...),
                       request: Request = None):
        principal = _principal(request)
        if principal is None:
            return _login_redirect()
        index = request.app.state.index
        item = index.get_review(review_id)
        if item is not None:
            item.status = "resolved"
            item.resolution = resolution
            item.resolver_id = principal.id
            index.save_review(item)
        return RedirectResponse(f"/admin/realms/{collection}?ok=review+resolved", status_code=303)

    @app.get("/admin/keys", response_class=HTMLResponse)
    def keys_page(request: Request, new_key: str = ""):
        if _principal(request) is None:
            return _login_redirect()
        keys = request.app.state.index.list_api_keys()
        rows = ""
        for k in keys:
            status = "revoked" if k["revoked"] else "active"
            revoke = ("" if k["revoked"] else
                      f'<form method="post" action="/admin/keys/{k["id"]}/revoke">'
                      '<button class="danger">revoke</button></form>')
            rows += (f'<tr><td>{html.escape(k["id"])}</td><td>{html.escape(k["name"] or "")}</td>'
                     f'<td>{html.escape(",".join(k["allowed_collections"]))}</td>'
                     f'<td>{html.escape(",".join(k["roles"]))}</td><td>{status}</td><td>{revoke}</td></tr>')
        table = (f'<table><tr><th>id</th><th>name</th><th>zones</th><th>roles</th><th>status</th><th></th></tr>'
                 f'{rows}</table>' if rows else "<p>No keys yet.</p>")
        shown = (f'<div class="ok">New key (copy now, shown once): <code>{html.escape(new_key)}</code></div>'
                 if new_key else "")
        create = (
            '<div class="card"><h3>Create key</h3>'
            '<form method="post" action="/admin/keys">'
            '<p>Name <input name="name"/></p>'
            '<p>Zones (comma-sep, or <code>*</code>) <input name="allowed_collections" value="*"/></p>'
            '<p>Roles (comma-sep: ingest,read,query,reviewer,admin) '
            '<input name="roles" value="ingest,query"/></p>'
            '<button type="submit">Create</button></form></div>')
        return _page("API keys", f'{shown}<div class="card"><h2>API keys</h2>{table}</div>{create}')

    @app.post("/admin/keys")
    def create_key(name: str = Form(""), allowed_collections: str = Form("*"),
                   roles: str = Form("ingest,query"), request: Request = None):
        if _principal(request) is None:
            return _login_redirect()
        import uuid
        from datetime import datetime, timezone
        from llmwiki.auth.stored import generate_key, hash_key
        raw = generate_key()
        cols = [c.strip() for c in allowed_collections.split(",") if c.strip()]
        rls = [r.strip() for r in roles.split(",") if r.strip()]
        request.app.state.index.create_api_key(
            id=f"key_{uuid.uuid4().hex[:12]}", key_hash=hash_key(raw), name=name,
            allowed_collections=cols, roles=rls, created_at=datetime.now(timezone.utc).isoformat())
        return RedirectResponse(f"/admin/keys?new_key={raw}", status_code=303)

    @app.post("/admin/keys/{key_id}/revoke")
    def revoke_key(key_id: str, request: Request = None):
        if _principal(request) is None:
            return _login_redirect()
        request.app.state.index.revoke_api_key(key_id)
        return RedirectResponse("/admin/keys", status_code=303)

    @app.get("/admin/help", response_class=HTMLResponse)
    def help_page(request: Request):
        if _principal(request) is None:
            return _login_redirect()
        body = """
        <div class="card"><h2>How gates &amp; rules work</h2>
        <p>A realm's decision <strong>pipeline</strong> is an ordered list of <strong>gates</strong>.
        Each gate holds <strong>rules</strong> from a categorized palette. Rules run in order,
        <em>first-match within a gate</em>, and the first gate to fire a terminal disposition
        short-circuits the chain. If nothing fires, the document is <code>ACCEPT</code>ed.</p>
        <p><span class="cat">Categories</span></p>
        <div class="gate"><strong>Validity</strong> — is this worth storing?
          <span class="rule">min_length</span><span class="rule">content_type</span>
          <span class="rule">regex_denylist</span><span class="rule">knowledge_worthiness</span></div>
        <div class="gate"><strong>Existence</strong> — do we already have it?
          <span class="rule">exact_duplicate</span><span class="rule">identity_match</span>
          <span class="rule">semantic_duplicate</span></div>
        <div class="gate"><strong>Update / Replace</strong> — change or supersede?
          <span class="rule">version_on_change</span><span class="rule">semantic_replace</span></div>
        <div class="gate"><strong>Routing</strong> — final disposition
          <span class="rule">confidence_route</span><span class="rule">accept</span></div>
        <p><span class="cat">Dispositions</span> ACCEPT · DUPLICATE · UPDATE · REPLACE · REVIEW · REJECT</p>
        <p>Semantic rules turn a score + confidence into a disposition; uncertainty routes to
        <code>REVIEW</code> rather than guessing. <code>semantic_replace</code> is review-by-default
        and only auto-replaces with a direction signal.</p></div>
        """
        return _page("gate concepts", body)
