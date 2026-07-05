"""Kernel pipeline: template method for the deny-short-circuit /
record-before-forward / audit sequence.

``Guard`` is the ABC every guard subclasses; ``Guard.handle`` is the one
place the sequence is built — a guard supplies the parts (abstract hooks),
the kernel owns the order, and a guard cannot reorder or skip a step because
it never sees the sequence, only its own hooks.

Sequence ``Guard.handle`` guarantees, in this order:

1. ``guard.parse`` — transport → an ``Intent``. No credential yet.
2. Recognize: the first matching row in ``guard.catalog`` yields the
   recognized action set for this intent — empty when nothing matches.
3. ``kernel_gates`` — guard-agnostic deny gates, all before ``enrich``:
   host allowlist, mode-gate writes, project allowlist, an unmatched/empty
   write, the criticality gate (any recognized action at or above
   ``Criticality.IRREVERSIBLE`` denies), the action gate (every recognized
   action must be enabled for the host).
4. ``guard.enrich`` — unpure lookups a check declared it needs.
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

from .actions import Action, Criticality
from .audit import AuditEvent, AuditLog
from .config import Config
from .model import Decision, Intent, StateView
from .recognizer import Recognizer, first_match
from .rules import R0, R3, R4, R6
from .state import State


def mode_gate_writes(host: str, cfg: Config) -> Optional[Decision]:
    """M0: deny a write to a host whose access mode is not ``read-write``.

    Per-host (step 05) — there is no more global mode. A ``closed`` host is
    already denied earlier by :func:`host_gate` (R6), so by the time this
    runs the only two possibilities left are ``read-only`` (deny) and
    ``read-write`` (allow).
    """
    access = cfg.access_mode(host)
    if access != "read-write":
        return Decision(False, R0, f"writes disabled for host {host!r} (access_mode={access!r})")
    return None


def host_gate(host: str, cfg: Config) -> Optional[Decision]:
    """M6: default-deny for a ``Host`` header outside the configured
    ``[[git.endpoint]]`` list (§2, §07 Punkt 8 follow-up).

    Real default-deny (step 03): an empty endpoint list denies every host, not
    "allow everything" — an operator lists every routable host explicitly.
    ``Config.host_allowed`` also denies a *known* host whose endpoint is
    currently ``closed`` (no usable read credential), so a host that
    ``UpstreamRouter.resolve`` would return ``None`` for is always denied here
    first, never reaching a "kernel_gates already denied" assertion downstream.
    Guard-agnostic and kernel-owned like every other gate in
    :func:`kernel_gates`, since every guard's request carries a host.
    """
    if not cfg.host_allowed(host):
        return Decision(False, R6, f"host {host!r} not in the multi-target allowlist")
    return None


def project_gate(project: str, project_allowed: Callable[[str], bool]) -> Optional[Decision]:
    """Resource allowlist — the single source of truth, shared by every guard.

    An empty ``project`` passes; projectless requests are gated by the guard's
    own ``decide`` (see ``guards.git.gitlab.recognizers``).

    ``project_allowed`` is a callable, not raw ``Config``, so a guard
    whose forge resolves numeric-id aliases can widen the check beyond
    ``cfg.project_allowed``'s path-only match.
    """
    if project and not project_allowed(project):
        return Decision(False, R6, f"project {project!r} not in allowlist")
    return None


def criticality_gate(recognized: frozenset[Action]) -> Optional[Decision]:
    """R4: any recognized action at or above ``Criticality.IRREVERSIBLE`` is
    never permitted, regardless of configuration.
    """
    blocked = sorted(a.id for a in recognized if a.criticality >= Criticality.IRREVERSIBLE)
    if not blocked:
        return None
    return Decision(False, R4, f"action {blocked[0]} is irreversible, never permitted")


def action_gate(
    recognized: frozenset[Action], effective: frozenset[str], host: str
) -> Optional[Decision]:
    """R6: every recognized action id must be enabled for ``host``.

    ``effective`` is the host's effective action ids, passed in rather than
    derived here, so a caller (production: ``Config.effective_actions``;
    tests: an arbitrary override) controls what "enabled" means without
    threading ``Config``/``host`` resolution through this function too.
    """
    missing = sorted(a.id for a in recognized if a.id not in effective)
    if not missing:
        return None
    return Decision(False, R6, f"action {missing[0]} not enabled for host {host!r}")


def kernel_gates(
    intent: Intent,
    cfg: Config,
    project_allowed: Callable[[str], bool],
    recognized: frozenset[Action],
    effective_actions: Optional[frozenset[str]] = None,
) -> Optional[Decision]:
    """The guard-agnostic deny gates, in kernel order (module docstring).

    One definition: ``Guard.handle`` runs this on every pipeline request, and
    each guard's ``full_decide`` composes it with the guard's pure ``decide``
    so unit tests exercise exactly the effective order — never a re-derived
    copy of it.

    ``recognized`` is this intent's whole action set (the guard's ``catalog``
    matched via ``first_match``, already computed by the caller since the
    catalog is guard-specific). An unmatched or empty-recognized *write*
    denies right here (R3) — a read falls through unchanged, since the
    project-bound read pass-through is the guard's own ``decide``'s job.
    """
    denied = host_gate(intent.host, cfg)
    if denied is None and intent.writes:
        denied = mode_gate_writes(intent.host, cfg)
    if denied is None:
        denied = project_gate(intent.project, project_allowed)
    if denied is None and intent.writes and not recognized:
        denied = Decision(False, R3, "no recognized action for this request")
    if denied is None:
        denied = criticality_gate(recognized)
    if denied is None:
        effective = (
            effective_actions
            if effective_actions is not None
            else frozenset(cfg.effective_actions(intent.host))
        )
        denied = action_gate(recognized, effective, intent.host)
    return denied


IntentT = TypeVar("IntentT", bound=Intent)


class Guard(ABC, Generic[IntentT]):
    """The parts a guard supplies to :meth:`handle`.

    ``name`` is the audit ``guard`` value (bare strings: ``"git"``/``"api"``).
    ``catalog``/``supported`` are the guard's recognizer table and the action
    set it declares itself capable of gating — the kernel recognizes and
    gates generically from these, so a guard hook does I/O
    (parse/enrich/record/forward/deny_response) or is pure default-deny logic
    (``decide``) only; the criticality/enablement checks are no longer a
    per-guard hook.
    """

    def __init__(self, cfg: Config, state: State, audit: AuditLog) -> None:
        self.cfg = cfg
        self.state = state
        self.audit = audit

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def catalog(self) -> tuple[Recognizer[IntentT], ...]:
        """This guard's recognizer table, most specific row first.

        The kernel matches it via ``first_match`` right after ``parse`` to
        get the intent's recognized action set.
        """
        ...

    @property
    @abstractmethod
    def supported(self) -> frozenset[Action]:
        """The action set this guard is capable of gating.

        The static ceiling — never what a deployment has actually enabled
        (``Config.effective_actions``).
        """
        ...

    @abstractmethod
    async def parse(self, request: Request) -> IntentT: ...

    @abstractmethod
    async def enrich(self, intent: IntentT) -> IntentT: ...

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

    def state_view(self, host: str) -> StateView:
        """Quota snapshot hook. Default: this guard's own core-only view (no
        domain state), locked until this guard reconciled. A guard backed by a
        domain (e.g. the git/REST-API branch/MR counters) overrides this to
        return the combined snapshot instead.

        ``host`` is the request's raw ``Host`` header (step 04, state-keying):
        the stateful quotas are per-endpoint now, so the snapshot must be
        scoped to the endpoint the request is actually addressed to.
        """
        return self.state.view(self.name, host)

    async def startup(self) -> None:
        """One-time, pre-serve setup.

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
        view = self.state_view(intent.host)

        match = first_match(self.catalog, intent)
        recognized: frozenset[Action] = (
            match.recognize(intent) if match is not None else frozenset()
        )

        decision = kernel_gates(intent, self.cfg, self.project_allowed, recognized)
        if decision is None:
            intent = await self.enrich(intent)
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
