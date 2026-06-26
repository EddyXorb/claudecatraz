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
from starlette.responses import Response

from .context import AppContext
from .errors import deny_json, git_reject_response
from .pktline import capabilities, parse_commands, read_until_flush
from .policy import Channel, ProxyRequest, TokenKind, check_ref, decide, project_gate
from .upstream import stream_upstream


def _project(request: Request) -> str:
    return request.path_params["project"]


def _service_token(service: str) -> TokenKind:
    return TokenKind.WRITE if service == "git-receive-pack" else TokenKind.READ


async def advertise(request: Request) -> Response:
    """GET …/info/refs?service=… — ref advertisement, passed through (W7.1).

    Discovery phase for every git operation: ``git clone``, ``git fetch``, and
    ``git push`` all start here. The client sends ``?service=git-upload-pack``
    (clone/fetch) or ``?service=git-receive-pack`` (push); the server replies with
    the list of refs it knows about.
    """
    ctx: AppContext = request.app.state.ctx
    project = _project(request)
    service = request.query_params.get("service", "git-upload-pack")

    denied = project_gate(project, ctx.cfg)
    if denied is not None:
        return deny_json(denied)

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
    """POST …/git-upload-pack — fetch, passed through with read-token (R1).

    Data phase of ``git clone`` and ``git fetch``: the client negotiates which
    objects it needs (want/have lines) and the server streams back a packfile.
    Read-only; never modifies the remote.
    """
    ctx: AppContext = request.app.state.ctx
    project = _project(request)

    denied = project_gate(project, ctx.cfg)
    if denied is not None:
        return deny_json(denied)

    resp = await ctx.upstream.git_post_stream(
        project,
        "git-upload-pack",
        body=request.stream(),
        content_type=request.headers.get(
            "content-type", "application/x-git-upload-pack-request"
        ),
        token=TokenKind.READ,
    )
    return stream_upstream(resp)


async def receive_pack(request: Request) -> Response:
    """POST …/git-receive-pack — parse ref commands, then stream (W7.3).

    Data phase of ``git push``: the client sends ref-update commands followed by
    a packfile with the new objects. This handler buffers only the command section
    (KB-sized) to inspect and police the refs, then streams the untouched pack
    upstream — SHA-preserving, without buffering the packfile in memory.
    """
    ctx: AppContext = request.app.state.ctx
    project = _project(request)
    correlation_id = str(uuid.uuid4())
    started = time.monotonic()

    head, rest = await read_until_flush(request.stream())
    commands = parse_commands(head)
    caps = capabilities(head)
    sideband = "side-band-64k" in caps or "side-band" in caps

    req = ProxyRequest(
        channel=Channel.GIT, project=project, method="POST", ref_commands=commands
    )
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
        _audit(
            ctx,
            project,
            commands,
            decision,
            correlation_id,
            state,
            started,
            status=None,
        )
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
        content_type=request.headers.get(
            "content-type", "application/x-git-receive-pack-request"
        ),
        token=TokenKind.WRITE,
        extra_headers=_forward_encoding(request),
    )
    _audit(
        ctx,
        project,
        commands,
        decision,
        correlation_id,
        state,
        started,
        status=resp.status_code,
    )
    return stream_upstream(resp)


def _forward_encoding(request: Request) -> dict[str, str]:
    # gzip stays gzip; the body is forwarded untouched (W7.4).
    enc = request.headers.get("content-encoding")
    return {"Content-Encoding": enc} if enc else {}


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
