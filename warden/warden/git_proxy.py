"""git Smart-HTTP filter proxy G1 (W7): four routes, stream-inspect pushes.

Reads (`info/refs`, `upload-pack`) stream through with the read-token (R1). For
`receive-pack` the command section is buffered, the ref updates are policed, and
on accept the *unchanged* body (head + PACK) is streamed upstream — SHA-preserving,
without ever buffering the packfile (W7.3).
"""

from __future__ import annotations

import time
import uuid

from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from .context import AppContext
from .errors import deny_json, git_reject_response
from .pktline import capabilities, parse_commands, read_until_flush
from .policy import ProxyRequest, TokenKind, check_ref, decide


def _project(request: Request) -> str:
    return request.path_params["project"]


def _service_token(service: str) -> TokenKind:
    return TokenKind.WRITE if service == "git-receive-pack" else TokenKind.READ


async def advertise(request: Request) -> Response:
    """GET …/info/refs?service=… — ref advertisement, passed through (W7.1)."""
    ctx: AppContext = request.app.state.ctx
    project = _project(request)
    service = request.query_params.get("service", "git-upload-pack")

    if not ctx.cfg.project_allowed(project):
        return deny_json(
            decision=_deny("R6", f"project {project!r} not in allowlist")
        )

    resp = await ctx.upstream.git_get(
        project,
        "info/refs",
        params={"service": service},
        token=_service_token(service),
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=ctx.upstream.response_headers(resp),
        media_type=resp.headers.get("content-type"),
    )


async def upload_pack(request: Request) -> Response:
    """POST …/git-upload-pack — fetch, passed through with read-token (R1)."""
    ctx: AppContext = request.app.state.ctx
    project = _project(request)
    if not ctx.cfg.project_allowed(project):
        return deny_json(_deny("R6", f"project {project!r} not in allowlist"))

    resp = await ctx.upstream.git_post_stream(
        project,
        "git-upload-pack",
        body=request.stream(),
        content_type=request.headers.get("content-type", "application/x-git-upload-pack-request"),
        token=TokenKind.READ,
    )

    async def body_iter():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=resp.status_code,
        headers=ctx.upstream.response_headers(resp),
        media_type=resp.headers.get("content-type"),
    )


async def receive_pack(request: Request) -> Response:
    """POST …/git-receive-pack — parse ref commands, then stream (W7.3)."""
    ctx: AppContext = request.app.state.ctx
    project = _project(request)
    correlation_id = str(uuid.uuid4())
    started = time.monotonic()

    head, rest = await read_until_flush(request.stream())
    commands = parse_commands(head)
    caps = capabilities(head)
    sideband = "side-band-64k" in caps or "side-band" in caps

    req = ProxyRequest(channel="git", project=project, method="POST", ref_commands=commands)
    state = ctx.state.view()
    decision = decide(req, state, ctx.cfg)

    refs = [c.ref for c in commands]
    if not decision.allow:
        # Per-ref decisions so the client sees which ref failed and why. Refs that
        # individually pass but were denied at the request level (e.g. R6 project)
        # report the overall reason, never a misleading "ok".
        per_ref = []
        for c in commands:
            d = check_ref(c, state, ctx.cfg)
            per_ref.append(d if not d.allow else decision)
        per_ref = per_ref or [decision]
        _audit(ctx, project, commands, decision, correlation_id, state, started, status=None)
        return git_reject_response(per_ref, refs or [""], sideband=sideband)

    # Record writes before the upstream call (idempotency / fail-safe, §6.11).
    for cmd in commands:
        ref = cmd.ref.removeprefix("refs/heads/")
        ctx.state.record_write("git", "push", ref)
        if cmd.is_create:
            ctx.state.add_branch(project, ref)

    async def body():
        yield head
        async for chunk in rest:
            yield chunk

    resp = await ctx.upstream.git_post_stream(
        project,
        "git-receive-pack",
        body=body(),
        content_type=request.headers.get("content-type", "application/x-git-receive-pack-request"),
        token=TokenKind.WRITE,
        extra_headers=_forward_encoding(request),
    )
    _audit(ctx, project, commands, decision, correlation_id, state, started, status=resp.status_code)

    async def body_iter():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=resp.status_code,
        headers=ctx.upstream.response_headers(resp),
        media_type=resp.headers.get("content-type"),
    )


def _forward_encoding(request: Request) -> dict[str, str]:
    # gzip stays gzip; the body is forwarded untouched (W7.4).
    enc = request.headers.get("content-encoding")
    return {"Content-Encoding": enc} if enc else {}


def _deny(rule: str, reason: str):
    from .policy import Decision

    return Decision(False, rule, reason)


def _audit(ctx, project, commands, decision, cid, state, started, *, status):
    ctx.audit.log(
        {
            "channel": "git",
            "correlation_id": cid,
            "method": "push",
            "project": project,
            "decision": "allow" if decision.allow else "deny",
            "rule": decision.rule,
            "reason": decision.reason,
            "refs": [f"{c.old[:8]}→{c.new[:8]} {c.ref}" for c in commands],
            "upstream_status": status,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "open_mrs": state.open_mrs,
            "open_branches": state.open_branches,
            "writes_last_hour": state.writes_last_hour,
        }
    )
