"""git Smart-HTTP guard (§03.3, W7): the receive-pack write pipeline's hooks,
plus the two thin, read-only handlers (advertise/upload-pack) that stay
outside that pipeline (§03.2's "dünne Handler" carve-out) — deduplicated via
the shared core gates instead of re-hand-rolling mode/project checks.

**Honest scope note for this migration step.** §03.3 assigns
``AppContext``/``Upstream`` to ``guards/gitlab_api`` (they hold GitLab
credentials and reconcile-against-GitLab-projects logic) — yet this
forge-agnostic guard still imports both from there, because both proxies have
always shared one context/credential-holder and giving each guard its own is
the explicit subject of §03.5 (forge abstraction) / §03.6 (process
boundaries), not this step ("Kernel-Extraktion + Intent-Split"). Documented
here rather than silently worked around.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping, Optional

from starlette.requests import Request
from starlette.responses import Response

from ...core.config import Config, normalize_project
from ...core.guard import Guard, mode_gate_off, mode_gate_writes, project_gate, run_guarded
from ...core.model import Decision, StateView, TokenKind
from ...errors import deny_json, git_reject_response
from ..gitlab_api.context import AppContext
from ..gitlab_api.upstream import stream_upstream
from . import policy
from .intent import GitPushIntent
from .pktline import capabilities, parse_commands, read_until_flush


def _project(request: Request) -> str:
    """Canonical project path (no ``.git``) for state keys, gate and upstream.

    git appends ``.git`` to the repo path while the reconcile/allowlist forms use
    the bare path; normalising here keeps the ``agent_branches`` key consistent
    so a branch is not counted twice and reconcile can prune push-recorded rows."""
    return normalize_project(str(request.path_params["project"]))


def _service_token(service: str) -> TokenKind:
    return TokenKind.WRITE if service == "git-receive-pack" else TokenKind.READ


async def advertise(request: Request) -> Response:
    """GET …/info/refs?service=… — ref advertisement, passed through (W7.1).

    Discovery phase for every git operation: ``git clone``, ``git fetch``, and
    ``git push`` all start here. The client sends ``?service=git-upload-pack``
    (clone/fetch) or ``?service=git-receive-pack`` (push); the server replies with
    the list of refs it knows about. Outside the kernel pipeline (§03.2): a pure
    read, gated with the same shared core helpers a write pipeline run uses.
    """
    ctx: AppContext = request.app.state.ctx
    project = _project(request)
    service = request.query_params.get("service", "git-upload-pack")

    denied = mode_gate_off(ctx.cfg)
    if denied is None:
        denied = project_gate(project, ctx.cfg)
    # R0: deny push discovery when writes are disabled — the write token must
    # not be sent upstream even for the info/refs phase of git-receive-pack.
    if denied is None and _service_token(service) == TokenKind.WRITE:
        denied = mode_gate_writes(ctx.cfg)
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

    denied = mode_gate_off(ctx.cfg)
    if denied is None:
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


def _forward_encoding(request: Request) -> dict[str, str]:
    # gzip stays gzip; the body is forwarded untouched (W7.4).
    enc = request.headers.get("content-encoding")
    return {"Content-Encoding": enc} if enc else {}


class GitGuard(Guard[GitPushIntent]):
    """The receive-pack write pipeline's hooks (§03.2) — used only via
    :func:`core.guard.run_guarded` from :func:`receive_pack` below."""

    # Audit ``guard`` value (§06-migration.md Schritt 6, F11: this JSONL
    # field used to be called ``channel``; the value itself is unchanged).
    @property
    def name(self) -> str:
        return "git"

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def parse(self, request: Request) -> GitPushIntent:
        """Buffer only the pkt-line command section (KB-sized) — the untouched
        PACK payload streams through :attr:`GitPushIntent.rest` (W7.3).
        """
        project = _project(request)
        head, rest = await read_until_flush(request.stream())
        commands = parse_commands(head)
        caps = capabilities(head)
        sideband = "side-band-64k" in caps or "side-band" in caps
        return GitPushIntent(
            _project=project,
            ref_commands=commands,
            head=head,
            rest=rest,
            content_type=request.headers.get(
                "content-type", "application/x-git-receive-pack-request"
            ),
            extra_headers=_forward_encoding(request),
            sideband=sideband,
        )

    async def enrich(self, intent: GitPushIntent) -> GitPushIntent:
        # git needs no unpure lookups before deciding (A10: unlike the REST
        # guard's MR-ownership check, no credential-backed lookup happens here).
        return intent

    def capability_gate(self, intent: GitPushIntent, cfg: Config) -> Optional[Decision]:
        return policy.capability_gate(intent, cfg)

    def decide(self, intent: GitPushIntent, state: StateView, cfg: Config) -> Decision:
        return policy.decide(intent, state, cfg)

    def record(self, intent: GitPushIntent, decision: Decision) -> None:
        """Record every ref write *before* the upstream call (§6.11)."""
        for cmd in intent.ref_commands:
            ref = cmd.ref.removeprefix("refs/heads/")
            self.ctx.state.record_write("git", "push", ref)
            if cmd.is_create:
                self.ctx.state.add_branch(intent.project, ref)

    async def forward(
        self, request: Request, intent: GitPushIntent, decision: Decision
    ) -> Response:
        async def body() -> AsyncIterator[bytes]:
            yield intent.head
            assert intent.rest is not None  # set by parse(); receive-pack always has a body
            async for chunk in intent.rest:
                yield chunk

        resp = await self.ctx.upstream.git_post_stream(
            intent.project,
            "git-receive-pack",
            body=body(),
            content_type=intent.content_type,
            token=TokenKind.WRITE,
            extra_headers=intent.extra_headers,
        )
        return stream_upstream(resp)

    def deny_response(
        self, intent: GitPushIntent, decision: Decision, state: StateView
    ) -> Response:
        """Per-ref rejection (W7.3): the client sees which ref failed and why.

        Refs that individually pass :func:`policy.check_ref` but were denied
        at the whole-push level (e.g. R6 project, or the aggregated §03.4
        capability gate) report the overall ``decision``, never a misleading
        "ok" — mirrors the pre-Schritt-5 ``git_proxy.receive_pack`` logic
        exactly, just relocated behind this hook so the kernel can call it
        without building a git-shaped response itself.
        """
        refs = [c.ref for c in intent.ref_commands]
        per_ref = []
        for cmd in intent.ref_commands:
            d = policy.check_ref(cmd, state, self.ctx.cfg)
            per_ref.append(d if not d.allow else decision)
        per_ref = per_ref or [decision]
        return git_reject_response(per_ref, refs or [""], sideband=intent.sideband)

    def audit_fields(self, intent: GitPushIntent) -> Mapping[str, Any]:
        return {"refs": [f"{c.old[:8]}→{c.new[:8]} {c.ref}" for c in intent.ref_commands]}


async def receive_pack(request: Request) -> Response:
    """POST …/git-receive-pack — parse ref commands, then stream (W7.3).

    Data phase of ``git push``: the client sends ref-update commands followed by
    a packfile with the new objects. Runs through the kernel pipeline
    (:func:`core.guard.run_guarded`) instead of hand-building the
    deny/record/forward sequence (F1's actual complaint about this handler).
    """
    ctx: AppContext = request.app.state.ctx
    return await run_guarded(GitGuard(ctx), request, ctx.cfg, ctx.state, ctx.audit)
