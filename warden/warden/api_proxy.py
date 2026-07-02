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
from typing import Any, Optional
from urllib.parse import parse_qsl, unquote

import httpx
from starlette.requests import Request
from starlette.responses import Response

from .api_endpoints import match_endpoint, mr_owned_by_claude
from .audit import build_event
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


async def _extract_fields(request: Request, body: bytes) -> dict[str, Any]:
    """Pull only the decision fields from body/query — no deep schema parsing (§6.9)."""
    fields: dict[str, Any] = dict(request.query_params)
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


async def _parse_request(request: Request) -> tuple[ProxyRequest, bytes, Optional[int]]:
    """Parse method/path/project/fields/body into the decision intent (W6, §6.9)."""
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
    return req, body, iid


async def _resolve_ownership(ctx: AppContext, req: ProxyRequest, iid: Optional[int]) -> None:
    """MR ownership lookup (W6.2), only when the matched endpoint needs it.

    Guard with writes_enabled: the write token must never be used in
    off/read-only mode. (decide() denies the write anyway, but the token must
    not be sent upstream first.)
    """
    ep = req.endpoint
    if not (ctx.cfg.writes_enabled and ep is not None and mr_owned_by_claude in ep.checks):
        return
    if iid is not None and req.project:
        req.mr_owner_ok = await ctx.mr_owned_by_claude(req.project, iid)


def _record_write(
    ctx: AppContext, req: ProxyRequest, decision: Decision, iid: Optional[int]
) -> None:
    """Record the write *before* the upstream call (idempotency / fail-safe, §6.11)."""
    if decision.token == TokenKind.WRITE and req.endpoint is not None:
        ctx.state.record_write("api", req.endpoint.kind, str(iid or req.project))


async def _forward(
    ctx: AppContext, request: Request, req: ProxyRequest, body: bytes, decision: Decision
) -> httpx.Response:
    return await ctx.upstream.open_rest(
        req.method,
        req.path,
        headers=dict(request.headers),
        content=body or None,
        token=decision.token,
    )


async def handle(request: Request) -> Response:
    ctx: AppContext = request.app.state.ctx
    correlation_id = str(uuid.uuid4())
    started = time.monotonic()

    req, body, iid = await _parse_request(request)
    await _resolve_ownership(ctx, req, iid)

    state = ctx.state.view()
    decision = decide(req, state, ctx.cfg)

    if not decision.allow:
        _audit(ctx, req, decision, correlation_id, state, started, upstream_status=None)
        return deny_json(decision)

    _record_write(ctx, req, decision, iid)

    resp = await _forward(ctx, request, req, body, decision)
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
        build_event(
            channel="api",
            correlation_id=cid,
            method=req.method,
            project=req.project,
            decision=decision,
            state=state,
            started=started,
            upstream_status=upstream_status,
            path=req.path,
            kind=req.endpoint.kind if req.endpoint else None,
        )
    )
