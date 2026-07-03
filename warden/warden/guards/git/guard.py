"""git Smart-HTTP guard (W7): the receive-pack write pipeline's hooks, plus
the read pipeline (advertise/upload-pack) — both run through
:meth:`core.guard.Guard.handle`.

**Honest scope note.** The guard imports ``AppContext``/``Upstream`` from
``gitlab_api`` because both guards have always shared one context/credential
holder; splitting that is a separate, later step.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping, Optional

from starlette.requests import Request
from starlette.responses import Response

from ...core.config import Config, normalize_project
from ...core.guard import Guard
from ...core.model import Decision, StateView, TokenKind
from ...core.rules import R1
from ...errors import deny_json, git_reject_response
from ..gitlab_api.context import AppContext
from ..gitlab_api.upstream import stream_upstream
from . import policy
from .intent import GitPushIntent, GitReadIntent
from .pktline import capabilities, parse_commands, read_until_flush


def _project(request: Request) -> str:
    """Canonical project path (no ``.git``) for state keys, gate and upstream.

    git appends ``.git`` to the repo path while the reconcile/allowlist forms use
    the bare path; normalising here keeps the ``agent_branches`` key consistent
    so a branch is not counted twice and reconcile can prune push-recorded rows."""
    return normalize_project(str(request.path_params["project"]))


class GitReadGuard(Guard[GitReadIntent]):
    """advertise/upload-pack (W7.1): read-only, except push discovery, which
    carries the write token but never a ref write (so ``record`` is a no-op).
    """

    @property
    def name(self) -> str:
        return "git"

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx.cfg, ctx.state, ctx.audit)
        self.ctx = ctx

    async def parse(self, request: Request) -> GitReadIntent:
        project = _project(request)
        if request.method == "GET":
            service = request.query_params.get("service", "git-upload-pack")
            return GitReadIntent(
                _project=project,
                _method=request.method,
                operation="advertise",
                service=service,
                _writes=(service == "git-receive-pack"),
            )
        return GitReadIntent(
            _project=project,
            _method=request.method,
            operation="upload-pack",
        )

    async def enrich(self, intent: GitReadIntent) -> GitReadIntent:
        return intent

    def capability_gate(self, intent: GitReadIntent, cfg: Config) -> Optional[Decision]:
        return None

    def decide(self, intent: GitReadIntent, state: StateView, cfg: Config) -> Decision:
        if intent.writes:
            return Decision(True, R1, "push discovery", TokenKind.WRITE)
        return Decision(True, R1, "read pass-through", TokenKind.READ)

    def record(self, intent: GitReadIntent, decision: Decision) -> None:
        # Reads and push discovery never count against the write quota.
        pass

    async def forward(
        self, request: Request, intent: GitReadIntent, decision: Decision
    ) -> Response:
        if intent.operation == "advertise":
            resp = await self.ctx.upstream.git_get(
                intent.project,
                "info/refs",
                params={"service": intent.service},
                token=decision.token,
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=self.ctx.upstream.response_headers(resp),
                media_type=resp.headers.get("content-type"),
            )
        resp = await self.ctx.upstream.git_post_stream(
            intent.project,
            "git-upload-pack",
            body=request.stream(),
            content_type=request.headers.get(
                "content-type", "application/x-git-upload-pack-request"
            ),
            token=decision.token,
        )
        return stream_upstream(resp)

    def deny_response(
        self, intent: GitReadIntent, decision: Decision, state: StateView
    ) -> Response:
        return deny_json(decision)

    def audit_fields(self, intent: GitReadIntent) -> Mapping[str, Any]:
        return {"op": intent.operation, "service": intent.service}


async def advertise(request: Request) -> Response:
    """GET …/info/refs?service=… — ref advertisement (W7.1).

    Discovery phase for every git operation: ``git clone``, ``git fetch``, and
    ``git push`` all start here.
    """
    ctx: AppContext = request.app.state.ctx
    return await GitReadGuard(ctx).handle(request)


async def upload_pack(request: Request) -> Response:
    """POST …/git-upload-pack — fetch, passed through with read-token (R1).

    Data phase of ``git clone`` and ``git fetch``: the client negotiates which
    objects it needs (want/have lines) and the server streams back a packfile.
    """
    ctx: AppContext = request.app.state.ctx
    return await GitReadGuard(ctx).handle(request)


def _forward_encoding(request: Request) -> dict[str, str]:
    # gzip stays gzip; the body is forwarded untouched (W7.4).
    enc = request.headers.get("content-encoding")
    return {"Content-Encoding": enc} if enc else {}


class GitGuard(Guard[GitPushIntent]):
    """The receive-pack write pipeline's hooks (§03.2) — used only via
    :meth:`Guard.handle` from :func:`receive_pack` below."""

    # Audit ``guard`` value (§06-migration.md Schritt 6, F11: this JSONL
    # field used to be called ``channel``; the value itself is unchanged).
    @property
    def name(self) -> str:
        return "git"

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx.cfg, ctx.state, ctx.audit)
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
    (:meth:`core.guard.Guard.handle`) instead of hand-building the
    deny/record/forward sequence (F1's actual complaint about this handler).
    """
    ctx: AppContext = request.app.state.ctx
    return await GitGuard(ctx).handle(request)
