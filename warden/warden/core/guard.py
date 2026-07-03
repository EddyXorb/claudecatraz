"""Kernel pipeline: template method for the deny-short-circuit /
record-before-forward / audit sequence.

:class:`Guard` is the ABC every guard subclasses; :meth:`Guard.handle` is the
one place the sequence is built — a guard supplies the parts (abstract hooks),
the kernel owns the order, and a guard cannot reorder or skip a step because it
never sees the sequence, only its own hooks.

Sequence ``Guard.handle`` guarantees, in this order:

1. ``guard.parse`` — transport → :class:`~warden.core.model.Intent`. No credential yet.
2. :func:`kernel_gates` — guard-agnostic deny gates:
   a. Mode-gate ``off`` — GitLab-disabled denies everything.
   b. Mode-gate ``read-only`` — set by parser, never by :class:`~warden.core.model.Decision`.
      Runs *before* ``enrich`` so credential-using lookups are unreachable in read-only/off mode.
   c. Resource allowlist — enforced once, not per-guard, before ``enrich``.
3. ``guard.enrich`` — unpure lookups a check declared it needs.
4. Capability invariants (``core.capabilities.FORBIDDEN``) via :meth:`Guard.capability_gate`.
5. ``guard.decide`` — pure, guard-specific, default-deny.
6. Audit — logged on *every* exit (allow or deny).
7. ``guard.record`` before ``guard.forward`` — write durably counted before upstream call.
8. ``guard.forward`` only reachable once ``decision.allow`` — deny calls ``guard.deny_response``.
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
    """Resource allowlist — the single source of truth, shared by every guard.

    An empty ``project`` passes; projectless requests are gated by the guard's
    own ``decide`` (see ``guards.gitlab_api.read_endpoints``).

    ``project_allowed`` is a callable, not raw ``Config``, so a guard
    whose forge resolves numeric-id aliases can widen the check beyond
    ``cfg.project_allowed``'s path-only match.
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
    ``decide`` so unit tests exercise exactly the effective order — never a
    re-derived copy of it.
    """
    denied = mode_gate_off(cfg)
    if denied is None and intent.writes:
        denied = mode_gate_writes(cfg)
    if denied is None:
        denied = project_gate(intent.project, project_allowed)
    return denied


IntentT = TypeVar("IntentT", bound=Intent)


class Guard(ABC, Generic[IntentT]):
    """The parts a guard supplies to :meth:`handle`.

    ``name`` is the audit ``guard`` value (bare strings: ``"git"``/``"api"``).
    Each hook either does I/O (parse/enrich/record/forward/deny_response) or is pure
    (capability_gate/decide) — only the pure half carries capability invariant and
    default-deny guarantees.
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
        """The Starlette routes this guard serves.

        The guard owns its own paths so ``app.py`` stays generic assembly
        instead of hand-listing every guard's endpoints.
        """
        ...

    def project_allowed(self, project: str) -> bool:
        """M6 membership hook. Default: the config allowlist by path
        (``cfg.project_allowed``). A guard whose forge resolves numeric-id
        aliases (e.g. ``ApiGuard``) overrides this to also accept those.
        """
        return self.cfg.project_allowed(project)

    def state_view(self) -> StateView:
        """Quota snapshot hook. Default: the core-only view (no domain state).
        A guard backed by a domain (e.g. the forge's branch/MR counters)
        overrides this to return the combined snapshot instead.
        """
        return self.state.view()

    async def startup(self) -> None:
        """One-time, pre-serve setup (e.g. resolving service-account id).

        Default no-op; a guard overrides only if needed.
        """
        return None

    async def reconcile(self) -> bool:
        """Rebuild this guard's quota/allowlist state from upstream truth.

        Run once before agent port opens, then periodically. Default no-op, returns True.
        """
        return True

    async def handle(self, request: Request) -> Response:
        """The kernel: guarantees the pipeline order regardless of guard.

        Uses only resource-agnostic collaborators (``cfg``/``state``/``audit``),
        never a guard's own I/O clients, which stay encapsulated in the subclass.
        """
        correlation_id = str(uuid.uuid4())
        started = time.monotonic()

        intent = await self.parse(request)
        view = self.state_view()

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
