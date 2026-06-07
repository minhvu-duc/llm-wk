from __future__ import annotations
import html
import json
import re
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from llmwiki.auth.base import AuthError
from llmwiki.config import CollectionConfig, default_pipeline
from llmwiki.rules.engine import build_pipeline
from llmwiki.rules.catalog import rule_catalog

_REALM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

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


_BUILDER_JS = r"""
var byId = {}; RULE_TYPES.forEach(function(rt){ byId[rt.id]=rt; });
var CATS = []; RULE_TYPES.forEach(function(rt){ if(CATS.indexOf(rt.category)<0) CATS.push(rt.category); });
var state = JSON.parse(JSON.stringify(PIPELINE));
function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function render(){
  var root=document.getElementById('builder'); root.innerHTML='';
  if(state.length===0){ root.innerHTML='<p class="cat">no gates — every document is ACCEPTed. add a gate below.</p>'; }
  state.forEach(function(g,gi){
    var card=document.createElement('div'); card.className='gate';
    var head=document.createElement('div');
    head.innerHTML='<input value="'+esc(g.gate)+'" placeholder="gate name" onchange="state['+gi+'].gate=this.value"/> '+
      '<button type="button" onclick="moveGate('+gi+',-1)">▲</button> '+
      '<button type="button" onclick="moveGate('+gi+',1)">▼</button> '+
      '<button type="button" class="danger" onclick="delGate('+gi+')">remove gate</button>';
    card.appendChild(head);
    (g.rules||[]).forEach(function(rl,ri){ card.appendChild(ruleRow(gi,ri,rl)); });
    var add=document.createElement('button'); add.type='button'; add.className='secondary'; add.textContent='+ rule';
    add.onclick=function(){ if(!state[gi].rules) state[gi].rules=[]; state[gi].rules.push({type:RULE_TYPES[0].id, params:{}}); render(); };
    card.appendChild(add); root.appendChild(card);
  });
}
function ruleRow(gi,ri,rl){
  var row=document.createElement('div');
  row.style.cssText='margin:6px 0;padding:6px;border:1px solid #e5e7eb;border-radius:6px;background:#fff';
  var opts='';
  CATS.forEach(function(c){
    opts+='<optgroup label="'+esc(c)+'">';
    RULE_TYPES.filter(function(r){return r.category===c;}).forEach(function(r){
      opts+='<option value="'+esc(r.id)+'"'+(r.id===rl.type?' selected':'')+'>'+esc(r.id)+'</option>';
    });
    opts+='</optgroup>';
  });
  var ctl=document.createElement('div');
  ctl.innerHTML='<select onchange="setType('+gi+','+ri+',this.value)">'+opts+'</select> '+
    '<button type="button" onclick="moveRule('+gi+','+ri+',-1)">▲</button> '+
    '<button type="button" onclick="moveRule('+gi+','+ri+',1)">▼</button> '+
    '<button type="button" class="danger" onclick="delRule('+gi+','+ri+')">x</button>';
  row.appendChild(ctl);
  var pc=document.createElement('div'); pc.style.marginTop='4px';
  var rt=byId[rl.type];
  (rt?rt.params:[]).forEach(function(p){ pc.appendChild(paramField(gi,ri,p,(rl.params||{})[p.name])); });
  row.appendChild(pc); return row;
}
function paramField(gi,ri,p,val){
  if(val===undefined||val===null) val=p.default;
  var w=document.createElement('label'); w.style.cssText='display:inline-block;margin:3px 10px 3px 0;font-size:13px';
  var k="setParam("+gi+","+ri+",'"+p.name+"',"; var inp;
  if(p.type==='bool'){ inp='<input type="checkbox" '+(val?'checked':'')+' onchange="'+k+'this.checked)"/>'; }
  else if(p.type==='enum'){ inp='<select onchange="'+k+'this.value)">'+p.options.map(function(o){return '<option'+(o===val?' selected':'')+'>'+esc(o)+'</option>';}).join('')+'</select>'; }
  else if(p.type==='int'){ inp='<input type="number" step="1" value="'+esc(val)+'" onchange="'+k+'Math.round(this.valueAsNumber||0))"/>'; }
  else if(p.type==='float'){ inp='<input type="number" step="0.01" value="'+esc(val)+'" onchange="'+k+'(isNaN(this.valueAsNumber)?0:this.valueAsNumber))"/>'; }
  else if(p.type==='list'){ inp='<input type="text" placeholder="comma,separated" value="'+esc((val||[]).join(','))+'" onchange="setParamList('+gi+','+ri+',\''+p.name+'\',this.value)"/>'; }
  else if(p.name==='rubric'){ inp='<textarea style="min-height:60px;width:320px;vertical-align:top" onchange="'+k+'this.value)">'+esc(val)+'</textarea>'; }
  else { inp='<input type="text" value="'+esc(val)+'" onchange="'+k+'this.value)"/>'; }
  w.innerHTML=esc(p.name)+' '+inp; return w;
}
function setType(gi,ri,t){ state[gi].rules[ri]={type:t,params:{}}; render(); }
function setParam(gi,ri,name,v){ if(!state[gi].rules[ri].params) state[gi].rules[ri].params={}; state[gi].rules[ri].params[name]=v; }
function setParamList(gi,ri,name,v){ setParam(gi,ri,name, v.split(',').map(function(s){return s.trim();}).filter(Boolean)); }
function addGate(){ state.push({gate:'gate'+(state.length+1),rules:[]}); render(); }
function delGate(gi){ state.splice(gi,1); render(); }
function delRule(gi,ri){ state[gi].rules.splice(ri,1); render(); }
function moveGate(gi,d){ var j=gi+d; if(j<0||j>=state.length) return; var t=state[gi]; state[gi]=state[j]; state[j]=t; render(); }
function moveRule(gi,ri,d){ var rs=state[gi].rules; var j=ri+d; if(j<0||j>=rs.length) return; var t=rs[ri]; rs[ri]=rs[j]; rs[j]=t; render(); }
function doSave(){ document.getElementById('pjson').value=JSON.stringify(state); return true; }
render();
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
    def home(request: Request, error: str = "", ok: str = ""):
        if _principal(request) is None:
            return _login_redirect()
        realms = request.app.state.index.list_collections()
        rows = "".join(f'<li><a href="/admin/realms/{html.escape(c)}">{html.escape(c)}</a></li>'
                       for c in realms) or "<li>(no realms yet)</li>"
        banner = (f'<div class="err">{html.escape(error)}</div>' if error else
                  f'<div class="ok">{html.escape(ok)}</div>' if ok else "")
        new_form = (
            '<div class="card"><h3>New realm</h3>'
            '<form method="post" action="/admin/realms" style="display:flex;gap:8px;align-items:center">'
            '<input name="name" placeholder="e.g. support"/> <button type="submit">Create</button>'
            '</form><p class="cat">lowercase letters, digits, - and _</p></div>')
        body = (f'{banner}<div class="card"><h2>Realms (zones)</h2><ul>{rows}</ul></div>'
                f'{new_form}'
                '<div class="card">Each realm is an isolated zone with its own gate pipeline, '
                'documents, and git history. Manage access under '
                '<a href="/admin/keys">API keys</a>.</div>')
        return _page("realms", body)

    @app.post("/admin/realms")
    def create_realm(name: str = Form(...), request: Request = None):
        if _principal(request) is None:
            return _login_redirect()
        name = name.strip()
        if not _REALM_RE.match(name):
            return RedirectResponse("/admin?error=invalid+realm+name+(use+a-z+0-9+-+_)",
                                    status_code=303)
        request.app.state.service.ensure_collection(name)
        return RedirectResponse(f"/admin/realms/{name}?ok=realm+created", status_code=303)

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
        rule_types_js = json.dumps(rule_catalog())
        pipeline_js = json.dumps(pipeline)
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
            f'<div class="card"><h3>Pipeline builder</h3>'
            f'<div id="builder"></div>'
            f'<button type="button" onclick="addGate()" style="margin-top:8px">+ gate</button>'
            f'<form id="pform" method="post" action="/admin/realms/{collection}/config" '
            f'onsubmit="return doSave()" style="margin-top:10px">'
            f'<input type="hidden" name="pipeline_json" id="pjson">'
            f'<button type="submit">Save pipeline</button></form></div>'
            f'<details class="card"><summary>Advanced (raw JSON)</summary>'
            f'<form method="post" action="/admin/realms/{collection}/config" style="margin-top:8px">'
            f'<textarea name="pipeline_json">{editor_json}</textarea><br>'
            f'<button type="submit" class="secondary">Save raw JSON</button></form></details>'
            f'<script>const RULE_TYPES={rule_types_js};const PIPELINE={pipeline_js};{_BUILDER_JS}</script>'
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
