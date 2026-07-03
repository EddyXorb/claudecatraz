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

from .audit import build_event
from .catalog import CatalogEntry, Location, match_endpoint
from .context import AppContext
from .errors import deny_json
from .model import Channel, Decision, ProxyRequest, StateView, TokenKind
from .policy import decide
from .rules import R6
from .upstream import stream_upstream

_PROJECT_RE = re.compile(r"/projects/([^/]+)")
_API_PREFIX = "/api/v4"


def _raw_rest_path(request: Request) -> str:
    """REST path after /api/v4, keeping percent-encoding (e.g. %2F in project ids).

    ASGI servers decode ``scope["path"]`` — which would turn ``group%2Fproj`` into
    a two-segment ``group/proj`` and break id extraction and forwarding. We read
    ``raw_path`` to preserve the encoding all the way to gitlab.com.

    Deliberately query-less: path matching/decision (``match_endpoint``,
    ``read_endpoints.match_read``) operate on the path alone. The query string
    is extracted separately (:func:`_raw_query`) and reattached only when
    forwarding (F12) — never folded back into this path.
    """
    raw = request.scope.get("raw_path")
    full = raw.decode("latin-1") if raw else request.url.path
    full = full.split("?", 1)[0]
    if full.startswith(_API_PREFIX):
        full = full[len(_API_PREFIX) :]
    return full or "/"


def _raw_query(request: Request) -> str:
    """Raw query string (percent-encoding intact), for the upstream URL only (F12).

    The *decision* reads decoded fields via ``request.query_params`` (folded
    into ``req.fields`` by :func:`_extract_fields`); this instead preserves the
    exact wire bytes GitLab must see (e.g. ``scope=blobs``, ``state=opened``) —
    without it, a query-dependent decision (:mod:`read_endpoints`) could pass on
    a value the upstream request never actually carries.
    """
    raw: bytes = request.scope.get("query_string", b"")
    return raw.decode("latin-1")


def _project_from_path(path: str) -> str:
    m = _PROJECT_RE.search(path)
    if not m:
        return ""
    return unquote(m.group(1))


def _iid_from_path(path: str) -> Optional[int]:
    m = re.search(r"/merge_requests/(\d+)", path)
    return int(m.group(1)) if m else None


def _parse_body_fields(body: bytes, content_type: str) -> dict[str, Any]:
    """Parse a JSON or form-encoded body into a flat field dict — no deep
    schema parsing (§6.9). Pure; a parse failure yields no fields, never an
    exception (default-deny handles the rest downstream).
    """
    if not body:
        return {}
    try:
        if "application/json" in content_type:
            data = json.loads(body)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, (str, int, bool))}
        elif "application/x-www-form-urlencoded" in content_type:
            return dict(parse_qsl(body.decode()))
    except (ValueError, UnicodeDecodeError):
        pass
    return {}


def _extract_fields(
    request: Request, body: bytes, ep: Optional[CatalogEntry]
) -> dict[str, Any]:
    """Pull the decision fields for this request (§6.9; F12 fix).

    For an unmatched write or any read (``ep is None``) this stays the old,
    query-only behaviour — the read path (``read_endpoints``) still decides
    on ``req.fields`` and is out of scope for this step (§06-migration.md
    Schritt 1 built it, Schritt 4 only extends the *write* catalog).

    For a matched catalog entry, F12 is fixed here: only the fields the entry
    *declares* (:attr:`CatalogEntry.decision_fields`) are read, each strictly
    from its declared location (body or query) — never a blind merge of
    both. A body-declared field that only appears in the query string (or the
    reverse) is simply absent from the decision, exactly as if the caller had
    never sent it at all.
    """
    if ep is None:
        return dict(request.query_params)
    query_fields = dict(request.query_params)
    body_fields = _parse_body_fields(body, request.headers.get("content-type", ""))
    fields: dict[str, Any] = {}
    for spec in ep.decision_fields:
        source = query_fields if spec.location is Location.QUERY else body_fields
        if spec.name in source:
            fields[spec.name] = source[spec.name]
    return fields


async def _parse_request(
    request: Request, ctx: AppContext
) -> tuple[ProxyRequest, bytes, Optional[int], str]:
    """Parse method/path/project/fields/body into the decision intent (W6, §6.9).

    Returns the raw query string too (F12) — kept out of ``ProxyRequest.path``
    (matching stays query-less) but needed by the caller to forward it.
    """
    rest_path = _raw_rest_path(request)
    raw_query = _raw_query(request)
    method = request.method.upper()
    project = _project_from_path(rest_path)

    body = b"" if method in ("GET", "HEAD", "OPTIONS") else await request.body()
    # §04.3: match against the effective table (Catalog × config), never the
    # catalog itself — a deployment's [api.endpoints] genuinely decides what
    # is reachable here.
    ep = (
        None
        if method in ("GET", "HEAD", "OPTIONS")
        else match_endpoint(ctx.cfg.effective_endpoints.entries, method, rest_path)
    )
    fields = _extract_fields(request, body, ep)
    iid = _iid_from_path(rest_path)

    req = ProxyRequest(
        channel=Channel.API,
        project=project,
        method=method,
        path=rest_path,
        endpoint=ep,
        fields=fields,
    )
    return req, body, iid, raw_query


def _needs_mr_owner(ep: CatalogEntry) -> bool:
    """F2 fix: does any check on the matched entry declare a need for the MR
    ownership lookup — instead of testing function identity
    (``mr_owned_by_claude in ep.checks``) against a hardcoded predicate.
    """
    return any("mr_owner" in check.needs for check in ep.checks)


async def _resolve_ownership(ctx: AppContext, req: ProxyRequest, iid: Optional[int]) -> None:
    """MR ownership lookup (W6.2), only when the matched endpoint needs it.

    Guard with writes_enabled: the write token must never be used in
    off/read-only mode. (decide() denies the write anyway, but the token must
    not be sent upstream first.)
    """
    ep = req.endpoint
    if not (ctx.cfg.writes_enabled and ep is not None and _needs_mr_owner(ep)):
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
    ctx: AppContext,
    request: Request,
    req: ProxyRequest,
    body: bytes,
    decision: Decision,
    raw_query: str,
) -> httpx.Response:
    # F12: the raw query is reattached here, at the transport boundary, never
    # folded into req.path — matching/decision stay query-less throughout.
    path = f"{req.path}?{raw_query}" if raw_query else req.path
    return await ctx.upstream.open_rest(
        req.method,
        path,
        headers=dict(request.headers),
        content=body or None,
        token=decision.token,
    )


async def handle(request: Request) -> Response:
    ctx: AppContext = request.app.state.ctx
    correlation_id = str(uuid.uuid4())
    started = time.monotonic()

    req, body, iid, raw_query = await _parse_request(request, ctx)
    await _resolve_ownership(ctx, req, iid)

    state = ctx.state.view()
    decision = decide(req, state, ctx.cfg)

    if not decision.allow:
        _audit(ctx, req, decision, correlation_id, state, started, upstream_status=None)
        return deny_json(decision)

    _record_write(ctx, req, decision, iid)

    resp = await _forward(ctx, request, req, body, decision, raw_query)
    _audit(ctx, req, decision, correlation_id, state, started, upstream_status=resp.status_code)
    return stream_upstream(resp)


def _enabled_via(ctx: AppContext, req: ProxyRequest) -> Optional[str]:
    """Audit marking for a non-default-activated catalog entry (§04.3).

    Returns ``None`` for the shipped default set (and for no match at all) —
    the field is additive and only shows up when it actually says something
    (§04.3 deviation from the ``rule = "gitlab.R3+enabled:…"`` sketch in
    ``04-policy-erweiterbarkeit.md``: a dedicated field instead of a
    rule-id suffix, documented there).
    """
    if req.endpoint is None:
        return None
    via = ctx.cfg.effective_endpoints.enabled_via.get(req.endpoint.id)
    return via if via and via != "default" else None


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
    extra: dict[str, Any] = {}
    enabled_via = _enabled_via(ctx, req)
    if enabled_via is not None:
        extra["enabled_via"] = enabled_via
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
            **extra,
        )
    )


async def deny_graphql(request: Request) -> Response:
    """`/api/graphql` — always 403, always audited, never contacts upstream (B5).

    GitLab's GraphQL API can express every write the REST filter blocks (create
    a tag, merge an MR) in a single mutation; routing it would silently bypass
    R2–R4. This handler makes the refusal *designed*, not merely "not wired up"
    (§06-migration.md Anti-Ziele) — the app's routes point every `/api/graphql`
    method here instead of proxying, so the deny is intentional and logged like
    any other decision.
    """
    ctx: AppContext = request.app.state.ctx
    correlation_id = str(uuid.uuid4())
    started = time.monotonic()
    decision = Decision(False, R6, "GraphQL is not permitted — unmodelled channel")
    state = ctx.state.view()
    ctx.audit.log(
        build_event(
            channel="api",
            correlation_id=correlation_id,
            method=request.method,
            project="",
            decision=decision,
            state=state,
            started=started,
            upstream_status=None,
            path=request.url.path,
            kind=None,
        )
    )
    return deny_json(decision)
