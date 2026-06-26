"""REST reverse-proxy: GET pass-through + filtered writes (W6).

Reads stream through with the read-token (R1); writes are matched against the
data-driven allowlist, ownership-checked, quota-checked, then forwarded with the
write-token — or denied with a 403 that never leaks a GitLab response.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Optional
from urllib.parse import parse_qsl, unquote

from starlette.requests import Request
from starlette.responses import StreamingResponse

from .api_endpoints import match_endpoint, mr_owned_by_claude
from .context import AppContext
from .errors import deny_json
from .model import Channel, Decision, ProxyRequest, StateView, TokenKind
from .policy import decide
from .upstream import stream_upstream

_PROJECT_RE = re.compile(r"/projects/([^/]+)")
_API_PREFIX = "/api/v4"


def _raw_rest_path(request: Request) -> str:
    """REST path after /api/v4, keeping percent-encoding (e.g. %2F in project ids).

    ASGI servers decode ``scope["path"]`` — which would turn ``group%2Fproj`` into
    a two-segment ``group/proj`` and break id extraction and forwarding. We read
    ``raw_path`` to preserve the encoding all the way to gitlab.com.
    """
    raw = request.scope.get("raw_path")
    full = raw.decode("latin-1") if raw else request.url.path
    full = full.split("?", 1)[0]
    if full.startswith(_API_PREFIX):
        full = full[len(_API_PREFIX) :]
    return full or "/"


def _project_from_path(path: str) -> str:
    m = _PROJECT_RE.search(path)
    if not m:
        return ""
    return unquote(m.group(1))


def _iid_from_path(path: str) -> Optional[int]:
    m = re.search(r"/merge_requests/(\d+)", path)
    return int(m.group(1)) if m else None


async def _extract_fields(request: Request, body: bytes) -> dict:
    """Pull only the decision fields from body/query — no deep schema parsing (§6.9)."""
    fields: dict = dict(request.query_params)
    if not body:
        return fields
    ctype = request.headers.get("content-type", "")
    try:
        if "application/json" in ctype:
            data = json.loads(body)
            if isinstance(data, dict):
                fields.update({k: v for k, v in data.items() if isinstance(v, (str, int, bool))})
        elif "application/x-www-form-urlencoded" in ctype:
            fields.update(dict(parse_qsl(body.decode())))
    except (ValueError, UnicodeDecodeError):
        pass
    return fields


async def handle(request: Request) -> StreamingResponse:
    ctx: AppContext = request.app.state.ctx
    correlation_id = str(uuid.uuid4())
    started = time.monotonic()

    rest_path = _raw_rest_path(request)
    method = request.method.upper()
    project = _project_from_path(rest_path)

    body = b"" if method in ("GET", "HEAD", "OPTIONS") else await request.body()
    fields = await _extract_fields(request, body)

    ep = None if method in ("GET", "HEAD", "OPTIONS") else match_endpoint(method, rest_path)
    iid = _iid_from_path(rest_path)
    req = ProxyRequest(
        channel=Channel.API,
        project=project,
        method=method,
        path=rest_path,
        endpoint=ep,
        fields=fields,
    )

    # Ownership lookup (W6.2) only when an endpoint needs it — before deciding.
    if ep is not None and mr_owned_by_claude in ep.checks:
        if iid is not None and project:
            req.mr_owner_ok = await ctx.mr_owned_by_claude(project, iid)

    state = ctx.state.view()
    decision = decide(req, state, ctx.cfg)

    if not decision.allow:
        _audit(ctx, req, decision, correlation_id, state, started, upstream_status=None)
        return deny_json(decision)

    # Record the write *before* the upstream call (idempotency / fail-safe, §6.11).
    if decision.token == TokenKind.WRITE and ep is not None:
        ctx.state.record_write("api", ep.kind, str(iid or project))

    resp = await ctx.upstream.open_rest(
        method,
        rest_path,
        headers=dict(request.headers),
        content=body or None,
        token=decision.token,
    )
    _audit(ctx, req, decision, correlation_id, state, started, upstream_status=resp.status_code)
    return stream_upstream(resp)


def _audit(
    ctx: AppContext,
    req: ProxyRequest,
    decision: Decision,
    cid: str,
    state: StateView,
    started: float,
    *,
    upstream_status: Optional[int],
) -> None:
    ctx.audit.log(
        {
            "channel": "api",
            "correlation_id": cid,
            "method": req.method,
            "path": req.path,
            "project": req.project,
            "decision": "allow" if decision.allow else "deny",
            "rule": decision.rule,
            "reason": decision.reason,
            "kind": req.endpoint.kind if req.endpoint else None,
            "upstream_status": upstream_status,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "open_mrs": state.open_mrs,
            "open_branches": state.open_branches,
            "writes_last_hour": state.writes_last_hour,
        }
    )
