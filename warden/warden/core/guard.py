"""Kernel pipeline template method (§03.2, F1; docs/design/architecture-generalization,
§03-guard-architektur.md §03.2, §06-migration.md Schritt 5).

:class:`Guard` is the ABC every guard subclasses; :meth:`Guard.handle` is the
one place the deny-short-circuit / record-before-forward / audit-on-every-path
sequence is built — a guard supplies the parts (the abstract hooks below), the
kernel owns the order, and a guard cannot reorder or skip a step because it
never sees the sequence, only its own hooks.

Sequence ``Guard.handle`` guarantees, in this order:

1. ``guard.parse`` — transport → an :class:`~warden.core.model.Intent`. No
   credential is used yet; this is just shaping the already-received request.
2. :func:`kernel_gates` — the guard-agnostic deny gates, one definition:
   a. Mode-gate ``off`` (M0) — GitLab-disabled denies everything, first.
   b. Mode-gate ``read-only`` (M0), decided from ``intent.writes`` alone —
      set by the guard's own parser, never derived from a
      :class:`~warden.core.model.Decision` (§03.2's precisification). This
      runs *before* ``enrich`` so an unpure, credential-using lookup (MR
      ownership, service-account resolution) is structurally unreachable in
      read-only/off mode — replacing the two manual ``writes_enabled``
      guards the pre-Schritt-5 code carried (``api_proxy.py:102``,
      ``git_proxy.py:62``).
   c. Resource allowlist (M6, :func:`project_gate`) — enforced once here
      instead of duplicated per guard, and also before ``enrich``: no lookup
      ever runs for a resource outside the allowlist.
3. ``guard.enrich`` — the unpure lookups a check declared it needs.
4. Capability invariants (§03.4, ``core.capabilities.FORBIDDEN``) via
   :meth:`Guard.capability_gate` — the guard's pure intent→capability mapping
   checked against the compiled-in deny set, before any allow-logic.
5. ``guard.decide`` — pure, guard-specific, default-deny.
6. Audit — logged on *every* exit above, allow or deny (A7).
7. ``guard.record`` before ``guard.forward`` — a write is durably counted
   before the upstream call ever happens (§6.11), never the other way round.
8. ``guard.forward`` only reachable once ``decision.allow`` — a deny instead
   calls ``guard.deny_response``, which gets the raw :class:`Decision` (and,
   for git's per-ref rejection shape, the quota snapshot) because a single
   status code is not enough to build every guard's error response.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Callable, Generic, Mapping, Optional, TypeVar

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .audit import AuditEvent, AuditLog
from .config import Config
from .model import Decision, Intent, StateView
from .rules import R0, R6
from .state import State


def mode_gate_off(cfg: Config) -> Optional[Decision]:
    """M0: deny every operation while GitLab is intentionally disabled."""
    if not cfg.gitlab_enabled:
        return Decision(False, R0, "GitLab disabled (GITLAB_MODE=off)")
    return None


def mode_gate_writes(cfg: Config) -> Optional[Decision]:
    """M0: deny a write while the deployment is read-only (or off)."""
    if not cfg.writes_enabled:
        return Decision(False, R0, f"writes disabled (GITLAB_MODE={cfg.gitlab_mode})")
    return None


def project_gate(project: str, project_allowed: Callable[[str], bool]) -> Optional[Decision]:
    """M6 resource allowlist — the single source of truth, shared by every guard.

    An empty ``project`` passes: an intent that carries no project at all
    (e.g. a projectless REST read) is gated elsewhere, by that guard's own
    ``decide`` (see ``guards.gitlab_api.read_endpoints``) — matching the
    pre-Schritt-5 ``policy.project_gate`` behaviour exactly.

    ``project_allowed`` is a callable, not the raw ``Config``, so a guard
    whose forge resolves numeric-id aliases (§03.5/03.6, ``ApiGuard``) can
    widen the check beyond ``cfg.project_allowed``'s path-only match without
    the kernel knowing anything about that forge concept.
    """
    if project and not project_allowed(project):
        return Decision(False, R6, f"project {project!r} not in allowlist")
    return None


def kernel_gates(
    intent: Intent, cfg: Config, project_allowed: Callable[[str], bool]
) -> Optional[Decision]:
    """The guard-agnostic deny gates, in kernel order (module docstring, step 2).

    One definition: :meth:`Guard.handle` runs this on every pipeline request,
    and each guard's ``full_decide`` composes it with the guard's pure
    ``decide`` so the startgate and unit tests exercise exactly the effective
    order — never a re-derived copy of it.
    """
    denied = mode_gate_off(cfg)
    if denied is None and intent.writes:
        denied = mode_gate_writes(cfg)
    if denied is None:
        denied = project_gate(intent.project, project_allowed)
    return denied


IntentT = TypeVar("IntentT", bound=Intent)


class Guard(ABC, Generic[IntentT]):
    """The parts a guard supplies to :meth:`handle` (§03.2/03.3).

    ``name`` is the audit ``guard`` value (§06-migration.md Schritt 6, F11:
    the JSONL field used to be called ``channel``; the bare string values —
    ``"git"``/``"api"`` — are unchanged). Every hook below either does I/O
    (parse/enrich/record/forward/deny_response) or is pure
    (capability_gate/decide) — only the pure half is what §03.4's capability
    invariant and default-deny guarantees rest on.
    """

    def __init__(self, cfg: Config, state: State, audit: AuditLog) -> None:
        self.cfg = cfg
        self.state = state
        self.audit = audit

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def parse(self, request: Request) -> IntentT: ...

    @abstractmethod
    async def enrich(self, intent: IntentT) -> IntentT: ...

    @abstractmethod
    def capability_gate(self, intent: IntentT, cfg: Config) -> Optional[Decision]: ...

    @abstractmethod
    def decide(self, intent: IntentT, state: StateView, cfg: Config) -> Decision: ...

    @abstractmethod
    def record(self, intent: IntentT, decision: Decision) -> None: ...

    @abstractmethod
    async def forward(self, request: Request, intent: IntentT, decision: Decision) -> Response: ...

    @abstractmethod
    def deny_response(self, intent: IntentT, decision: Decision, state: StateView) -> Response: ...

    @abstractmethod
    def audit_fields(self, intent: IntentT) -> Mapping[str, Any]: ...

    @abstractmethod
    def routes(self) -> list[Route]:
        """The Starlette routes this guard serves (§03.5/03.6): the guard owns
        its own paths so ``app.py`` can stay generic assembly (``[r for g in
        ctx.guards for r in g.routes()]``) instead of hand-listing every
        guard's endpoints.
        """
        ...

    def project_allowed(self, project: str) -> bool:
        """M6 membership hook. Default: the config allowlist by path
        (``cfg.project_allowed``). A guard whose forge resolves numeric-id
        aliases (e.g. ``ApiGuard``) overrides this to also accept those.
        """
        return self.cfg.project_allowed(project)

    async def startup(self) -> None:
        """One-time, pre-serve setup (§03.5/03.6) — e.g. resolving the
        service-account id. Default no-op; a guard overrides only if it needs
        this hook.
        """
        return None

    async def reconcile(self) -> bool:
        """Rebuild this guard's quota/allowlist state from upstream truth
        (§03.5/03.6, W8.2) — run once before the agent port opens and then
        periodically. Default no-op, returns True (nothing to reconcile).
        """
        return True

    async def handle(self, request: Request) -> Response:
        """The kernel (§03.2): guarantees the pipeline order regardless of guard.

        Uses only ``self.cfg``/``self.state``/``self.audit`` — the resource-
        agnostic collaborators (M0/M6 gates read ``cfg``; quota fail-safety
        reads ``state``; A7 needs ``audit``), never a guard's own I/O clients
        (upstream credentials, ownership caches, …), which stay encapsulated
        in the guard subclass itself.
        """
        correlation_id = str(uuid.uuid4())
        started = time.monotonic()

        intent = await self.parse(request)
        view = self.state.view()

        decision = kernel_gates(intent, self.cfg, self.project_allowed)
        if decision is None:
            intent = await self.enrich(intent)
            decision = self.capability_gate(intent, self.cfg)
        if decision is None:
            decision = self.decide(intent, view, self.cfg)

        upstream_status: Optional[int]
        if decision.allow:
            self.record(intent, decision)
            response = await self.forward(request, intent, decision)
            upstream_status = response.status_code
        else:
            response = self.deny_response(intent, decision, view)
            upstream_status = None

        self.audit.log(
            AuditEvent(
                guard=self.name,
                correlation_id=correlation_id,
                method=intent.method,
                project=intent.project,
                decision=decision,
                state=view,
                started=started,
                upstream_status=upstream_status,
                extra=self.audit_fields(intent),
            )
        )
        return response
