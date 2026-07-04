"""git Smart-HTTP guard: all three operations — advertise, upload-pack,
receive-pack — dispatched via :class:`GitGuard` hooks per-operation.

Forge-agnostic in logic (no GitLab vocabulary). Credential injection and
response streaming come from the forge-neutral :mod:`warden.core.transport`
(§07 Punkt 6, step 1) — this guard never imports ``guards.gitlab.upstream``.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping, Optional

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...core.audit import AuditLog
from ...core.config import Config, normalize_project
from ...core.guard import Guard
from ...core.model import Decision, StateView, TokenKind
from ...core.rules import R1
from ...core.state import State
from ...core.transport import UpstreamRouter, stream_upstream
from ...errors import deny_json
from . import policy
from .errors import git_reject_response
from .intent import GitIntent
from .pktline import capabilities, parse_commands, read_until_flush
from .reconcile import reconcile_branches
from .state import BranchState


def _project(request: Request) -> str:
    """Canonical project path (no ``.git``) for state keys, gate and upstream.

    git appends ``.git`` to the repo path while the reconcile/allowlist forms use
    the bare path; normalising here keeps the ``agent_branches`` key consistent
    so a branch is not counted twice and reconcile can prune push-recorded rows."""
    return normalize_project(str(request.path_params["project"]))


def _forward_encoding(request: Request) -> dict[str, str]:
    # Forward content-encoding unchanged; body is forwarded untouched.
    enc = request.headers.get("content-encoding")
    return {"Content-Encoding": enc} if enc else {}


def _content_length(request: Request) -> Optional[int]:
    """Read ``Content-Length`` for the cheap push-size gate (R5, §07 Punkt 6.3).

    No packfile parsing: an absent/non-numeric header (chunked transfer)
    yields ``None`` — the size gate simply has nothing to check then.
    """
    raw = request.headers.get("content-length")
    return int(raw) if raw and raw.isdigit() else None


class GitGuard(Guard[GitIntent]):
    """All three git Smart-HTTP operations dispatched via :meth:`Guard.handle`.

    Reads (advertise/upload-pack) are read-only except push discovery, which
    carries the write token but never performs a ref write. receive-pack is always a write.
    """

    @property
    def name(self) -> str:
        return "git"

    def __init__(self, cfg: Config, state: State, audit: AuditLog, router: UpstreamRouter) -> None:
        super().__init__(cfg, state, audit)
        self.router = router
        self.branch_state = BranchState(state.store)

    def routes(self) -> list[Route]:
        return [
            Route("/git/{project:path}/info/refs", self.handle, methods=["GET"]),
            Route("/git/{project:path}/git-upload-pack", self.handle, methods=["POST"]),
            Route("/git/{project:path}/git-receive-pack", self.handle, methods=["POST"]),
        ]

    def state_view(self, host: str) -> StateView:
        """This guard's own per-guard lock + its own branch counter, scoped to
        ``host`` (§07 Punkt 6 step 4; per-endpoint since step 04). Locked
        until *this* guard reconciled — a broken REST-API upstream never
        locks git, and vice versa.

        ``host`` is normalised but not resolved/validated here: this runs
        *before* the kernel's ``host_gate`` (§2), so an unrecognised host must
        not raise — it simply queries a key nothing was ever recorded under,
        yielding harmless zero counts; ``host_gate`` denies the request right
        after regardless of what this view reports.
        """
        if not self.state.is_reconciled(self.name):
            return StateView(locked=True)
        key = self.cfg.normalize_host(host)
        return StateView(
            open_branches=self.branch_state.open_branches(key),
            writes_last_hour=self.state.writes_last_hour(key),
            locked=False,
        )

    async def reconcile(self) -> bool:
        """Rebuild the branch-quota counter from upstream truth (§07 Punkt 6, step 4).

        Own reconcile, independent of the REST-API guard's MR reconcile: rebuilds
        only this guard's own branch counter and, on success, unlocks only its own
        per-guard lock (:meth:`~warden.core.state.State.mark_reconciled`). A
        failure here leaves *this* guard fail-safe-locked but never touches the
        REST-API guard's lock — one guard's permanently unreachable upstream can
        never block the other.

        No endpoints configured (the former ``GITLAB_MODE=off``) makes no
        upstream call either: :func:`~warden.core.transport.for_each_host_project`
        simply iterates zero hosts and returns ``True``, so this guard still
        unlocks and the warden serves (and then denies) instead of staying
        fail-safe-locked.
        """
        ok = await reconcile_branches(self.cfg, self.router, self.branch_state)
        if ok:
            self.state.mark_reconciled(self.name)
        return ok

    async def parse(self, request: Request) -> GitIntent:
        """Buffer only the pkt-line command section (KB-sized) for receive-pack;
        the untouched PACK payload streams through :attr:`GitIntent.rest`."""
        project = _project(request)
        host = request.headers.get("host", "")
        if request.method == "GET":
            service = request.query_params.get("service", "git-upload-pack")
            return GitIntent(
                _project=project,
                operation="advertise",
                _method="GET",
                _host=host,
                service=service,
                _writes=(service == "git-receive-pack"),
            )
        if request.url.path.endswith("git-receive-pack"):
            head, rest = await read_until_flush(request.stream())
            commands = parse_commands(head)
            caps = capabilities(head)
            sideband = "side-band-64k" in caps or "side-band" in caps
            return GitIntent(
                _project=project,
                operation="receive-pack",
                _method="push",
                _host=host,
                _writes=True,
                ref_commands=commands,
                head=head,
                rest=rest,
                content_type=request.headers.get(
                    "content-type", "application/x-git-receive-pack-request"
                ),
                extra_headers=_forward_encoding(request),
                sideband=sideband,
                push_bytes=_content_length(request),
            )
        return GitIntent(
            _project=project,
            operation="upload-pack",
            _method="POST",
            _host=host,
        )

    async def enrich(self, intent: GitIntent) -> GitIntent:
        # git needs no unpure lookups; unlike REST guard's MR
        # source-branch-namespace check, no credential-backed lookup happens here.
        return intent

    def capability_gate(self, intent: GitIntent, cfg: Config) -> Optional[Decision]:
        if intent.operation == "receive-pack":
            return policy.capability_gate(intent, cfg)
        return None

    def decide(self, intent: GitIntent, state: StateView, cfg: Config) -> Decision:
        if intent.operation == "receive-pack":
            return policy.decide(intent, state, cfg)
        if intent.writes:
            return Decision(True, R1, "push discovery", TokenKind.WRITE)
        return Decision(True, R1, "read pass-through", TokenKind.READ)

    def record(self, intent: GitIntent, decision: Decision) -> None:
        """Record every ref write before the upstream call to ensure a crash never loses a write.

        Reads and push discovery never count against the write quota.
        """
        if intent.operation != "receive-pack":
            return
        host = self.cfg.resolve_target_host(intent.host)
        assert host is not None, "kernel_gates already denied an unresolved host"
        for cmd in intent.ref_commands:
            ref = cmd.ref.removeprefix("refs/heads/")
            self.state.record_write("git", host, "push", ref)
            if cmd.is_create:
                self.branch_state.add_branch(host, intent.project, ref)

    async def forward(self, request: Request, intent: GitIntent, decision: Decision) -> Response:
        transport = self.router.resolve(intent.host)
        assert transport is not None, "kernel_gates already denied an unresolved host"
        if intent.operation == "advertise":
            resp = await transport.git_get(
                intent.project,
                "info/refs",
                params={"service": intent.service},
                token=decision.token,
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=transport.response_headers(resp),
                media_type=resp.headers.get("content-type"),
            )
        if intent.operation == "upload-pack":
            resp = await transport.git_post_stream(
                intent.project,
                "git-upload-pack",
                body=request.stream(),
                content_type=request.headers.get(
                    "content-type", "application/x-git-upload-pack-request"
                ),
                token=decision.token,
            )
            return stream_upstream(resp)

        async def body() -> AsyncIterator[bytes]:
            yield intent.head
            assert intent.rest is not None  # set by parse(); receive-pack always has a body
            async for chunk in intent.rest:
                yield chunk

        resp = await transport.git_post_stream(
            intent.project,
            "git-receive-pack",
            body=body(),
            content_type=intent.content_type,
            token=TokenKind.WRITE,
            extra_headers=intent.extra_headers,
        )
        return stream_upstream(resp)

    def deny_response(self, intent: GitIntent, decision: Decision, state: StateView) -> Response:
        """Per-ref rejection for receive-pack: client sees which ref failed and why.

        Refs that individually pass :func:`policy.check_ref` but were denied at
        the whole-push level (e.g. R6 project or capability gate) report the overall
        ``decision``, never a misleading "ok". advertise/upload-pack denials get
        a plain JSON body instead — there is no per-ref shape for a read.
        """
        if intent.operation != "receive-pack":
            return deny_json(decision)
        refs = [c.ref for c in intent.ref_commands]
        rules = self.cfg.effective_rules(intent.host)
        max_open_branches, max_writes_per_hour = rules.max_open_branches, rules.max_writes_per_hour
        assert max_open_branches is not None and max_writes_per_hour is not None, (
            "effective_rules always resolves every field to a concrete built-in default"
        )
        per_ref = []
        for cmd in intent.ref_commands:
            d = policy.check_ref(cmd, state, self.cfg, max_open_branches, max_writes_per_hour)
            per_ref.append(d if not d.allow else decision)
        per_ref = per_ref or [decision]
        return git_reject_response(per_ref, refs or [""], sideband=intent.sideband)

    def audit_fields(self, intent: GitIntent) -> Mapping[str, Any]:
        if intent.operation == "receive-pack":
            return {"refs": [f"{c.old[:8]}→{c.new[:8]} {c.ref}" for c in intent.ref_commands]}
        return {"op": intent.operation, "service": intent.service}
