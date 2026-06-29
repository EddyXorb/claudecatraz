"""Starlette app + routing: API vs. git, agent port vs. admin port (W3, W4)."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import api_proxy, git_proxy
from .context import AppContext


def create_app(ctx: AppContext) -> Starlette:
    """Agent-facing app on port 8080: API proxy + git Smart-HTTP (W4)."""
    # The /git/ prefix is load-bearing: it separates git Smart-HTTP routes from
    # /api/v4/… on the same port. Repos keep their canonical remote URL
    # (https://gitlab.com/…); the entrypoint injects a global git insteadOf rewrite
    # (https://gitlab.com/ → http://gitlab-warden:8080/git/) so the prefix is added
    # transparently at transport time without touching .git/config. See W3.1.
    routes = [
        Route("/git/{project:path}/info/refs", git_proxy.advertise, methods=["GET"]),
        Route("/git/{project:path}/git-upload-pack", git_proxy.upload_pack, methods=["POST"]),
        Route("/git/{project:path}/git-receive-pack", git_proxy.receive_pack, methods=["POST"]),
        Route(
            "/api/v4/{rest:path}",
            api_proxy.handle,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
        ),
        Route("/healthz", _healthz, methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    app.state.ctx = ctx
    return app


def create_admin_app(ctx: AppContext) -> Starlette:
    """Admin app on port 9090: healthz + read-only log tail + viewer (W3, §6.8, O.4)."""
    routes = [
        Route("/healthz", _healthz, methods=["GET"]),
        Route("/audit", _audit_tail, methods=["GET"]),
        Route("/", _viewer, methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    app.state.ctx = ctx
    return app


async def _healthz(request: Request) -> JSONResponse:
    ctx: AppContext = request.app.state.ctx
    return JSONResponse(
        {
            "status": "ok",
            "reconciled": ctx.state.is_reconciled(),
            "service_account_id": ctx.service_account_id,
        }
    )


async def _audit_tail(request: Request) -> Response:
    """Read-only tail of the JSONL audit log (admin net only)."""
    ctx: AppContext = request.app.state.ctx
    path = ctx.cfg.audit_log_path
    try:
        with open(path, "rb") as fh:
            lines = fh.readlines()[-200:]
    except OSError:
        return PlainTextResponse("", status_code=200)
    return PlainTextResponse(b"".join(lines).decode("utf-8", "replace"))


async def _viewer(request: Request) -> HTMLResponse:
    """Static log viewer (O.4): filters JSONL by channel/rule/decision/project."""
    return HTMLResponse(_VIEWER_HTML)


_VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Warden Audit Log</title>
<style>
body{font-family:monospace;font-size:13px;margin:0;background:#1e1e1e;color:#d4d4d4}
h1{padding:10px 16px;margin:0;font-size:15px;background:#252526;border-bottom:1px solid #444}
#filters{display:flex;gap:10px;padding:8px 16px;background:#252526;border-bottom:1px solid #444;flex-wrap:wrap;align-items:center}
#filters label{display:flex;align-items:center;gap:4px}
#filters select,#filters input{background:#3c3c3c;color:#d4d4d4;border:1px solid #555;padding:3px 6px;border-radius:3px}
#reload{padding:4px 10px;background:#0e639c;color:#fff;border:none;border-radius:3px;cursor:pointer}
#reload:hover{background:#1177bb}
#count{margin-left:auto;color:#888;font-size:12px}
table{width:100%;border-collapse:collapse}
thead th{position:sticky;top:0;background:#2d2d2d;color:#ccc;font-weight:bold;padding:5px 8px;text-align:left;border-bottom:1px solid #444;white-space:nowrap}
tbody tr{border-bottom:1px solid #2a2a2a}
tbody tr:hover{background:#2a2d2e}
td{padding:4px 8px;vertical-align:top}
.deny{color:#f48771;font-weight:bold}
.allow{color:#89d185}
.r4{background:rgba(200,50,50,.18)}
.r5{background:rgba(200,120,30,.18)}
.ts{white-space:nowrap;color:#888}
.rule{white-space:nowrap;font-weight:bold}
.path{word-break:break-all;max-width:280px;color:#9cdcfe}
.proj{color:#ce9178}
.rsn{color:#aaa;max-width:220px;word-break:break-word}
#err{color:#f48771;padding:8px 16px}
</style>
</head>
<body>
<h1>Warden Audit Log</h1>
<div id="filters">
  <label>Channel <select id="fc"><option value="">all</option><option>api</option><option>git</option></select></label>
  <label>Decision <select id="fd"><option value="">all</option><option>allow</option><option>deny</option></select></label>
  <label>Rule <input id="fr" placeholder="e.g. R4" style="width:60px"></label>
  <label>Project <input id="fp" placeholder="path filter" style="width:160px"></label>
  <button id="reload" onclick="load()">&#8635; Load</button>
  <span id="count"></span>
</div>
<div id="err"></div>
<table>
<thead><tr><th>Time</th><th>Channel</th><th>Method</th><th>Decision</th><th>Rule</th><th>Project</th><th>Path</th><th>Reason</th><th>ms</th></tr></thead>
<tbody id="tb"></tbody>
</table>
<script>
let rows=[];
function fmt(ts){if(!ts)return'';const d=new Date(ts*1000);return d.toISOString().replace('T',' ').replace(/\\.\\d+Z$/,' UTC')}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
async function load(){
  document.getElementById('err').textContent='';
  try{
    const r=await fetch('/audit');
    if(!r.ok)throw new Error('HTTP '+r.status);
    const t=await r.text();
    rows=t.trim().split('\\n').filter(Boolean).map(l=>{try{return JSON.parse(l)}catch{return null}}).filter(Boolean).reverse();
    render();
  }catch(e){document.getElementById('err').textContent='Error: '+e}
}
function render(){
  const ch=document.getElementById('fc').value;
  const dec=document.getElementById('fd').value;
  const rule=document.getElementById('fr').value.toUpperCase();
  const proj=document.getElementById('fp').value.toLowerCase();
  const f=rows.filter(r=>
    (!ch||r.channel===ch)&&
    (!dec||r.decision===dec)&&
    (!rule||(r.rule||'').toUpperCase().includes(rule))&&
    (!proj||(r.project||'').toLowerCase().includes(proj))
  );
  document.getElementById('count').textContent=f.length+' / '+rows.length+' entries';
  const tb=document.getElementById('tb');
  tb.innerHTML='';
  for(const r of f){
    const ru=(r.rule||'').toUpperCase();
    const cls=r.decision==='deny'&&ru==='R4'?'r4':r.decision==='deny'&&ru==='R5'?'r5':'';
    const tr=document.createElement('tr');
    if(cls)tr.className=cls;
    tr.innerHTML=`<td class="ts">${fmt(r.ts)}</td><td>${esc(r.channel)}</td><td>${esc(r.method)}</td>`+
      `<td class="${r.decision==='deny'?'deny':'allow'}">${esc(r.decision)}</td>`+
      `<td class="rule">${esc(r.rule)}</td><td class="proj">${esc(r.project)}</td>`+
      `<td class="path">${esc(r.path)}</td><td class="rsn">${esc(r.reason)}</td>`+
      `<td>${r.latency_ms!=null?r.latency_ms:''}</td>`;
    tb.appendChild(tr);
  }
}
['fc','fd','fr','fp'].forEach(id=>document.getElementById(id).addEventListener('input',render));
load();
setInterval(load,30000);
</script>
</body>
</html>"""
