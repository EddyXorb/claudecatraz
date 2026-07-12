"""Kernel pipeline: template method for the deny-short-circuit /
record-before-forward / audit sequence. Guard.handle order: parse,
recognize, kernel_gates, enrich, decide, audit every exit, record
before forward.
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
from .recognizer import Recognizer, first_recognized
from .state import State


def write_credential_gate(host: str, cfg: Config) -> Optional[Decision]:
    """Deny a request needing the write token on a host with no write
    credential. Catches git push discovery, which recognizes as repo.read
    (always enabled) yet still needs the write token upstream — no
    action-level gate would otherwise catch it.
    """
    access = cfg.access_mode(host)
    if access != "read-write":
        return Decision(
            False, f"write token unavailable for host {host!r} (access_mode={access!r})"
        )
    return None


def host_gate(host: str, cfg: Config) -> Optional[Decision]:
    """Default-deny for a Host header outside the configured endpoint list.

    An empty endpoint list denies every host, not "allow everything".
    Also denies a known host whose endpoint is currently closed (no
    usable read credential).
    """
    if not cfg.host_allowed(host):
        return Decision(False, f"host {host!r} not in the multi-target allowlist")
    return None


def project_gate(
    project: str, host: str, project_allowed: Callable[[str, str], bool]
) -> Optional[Decision]:
    """Resource allowlist — the single source of truth, shared by every guard.

    An empty project passes; projectless requests are gated by the guard's
    own decide. project_allowed is a callable, not raw Config, so a guard
    can widen the check beyond a path-only match. Host-scoped: the same
    project id authorises different things on different hosts.
    """
    if project and not project_allowed(host, project):
        return Decision(False, f"project {project!r} not in allowlist")
    return None


def criticality_gate(recognized: frozenset[Action]) -> Optional[Decision]:
    """Any recognized action at or above Criticality.IRREVERSIBLE is
    never permitted, regardless of configuration.
    """
    blocked = sorted(a.id for a in recognized if a.criticality >= Criticality.IRREVERSIBLE)
    if not blocked:
        return None
    return Decision(False, f"action {blocked[0]} is irreversible, never permitted")


def action_gate(
    recognized: frozenset[Action], effective: frozenset[str], host: str
) -> Optional[Decision]:
    """Every recognized action id must be enabled for host.

    effective is passed in rather than derived here, so callers (production
    vs. tests) control what "enabled" means without threading Config through.
    """
    missing = sorted(a.id for a in recognized if a.id not in effective)
    if not missing:
        return None
    return Decision(False, f"action {missing[0]} not enabled for host {host!r}")


def kernel_gates(
    intent: Intent,
    cfg: Config,
    project_allowed: Callable[[str, str], bool],
    recognized: frozenset[Action],
    effective_actions: Optional[frozenset[str]] = None,
) -> Optional[Decision]:
    """The guard-agnostic deny gates, in kernel order.

    recognized is this intent's whole action set. An unmatched or
    empty-recognized write denies right here; a read falls through
    unchanged to the guard's own decide.
    """
    denied = host_gate(intent.host, cfg)
    if denied is None and intent.needs_write:
        denied = write_credential_gate(intent.host, cfg)
    if denied is None:
        denied = project_gate(intent.project, intent.host, project_allowed)
    if denied is None and intent.needs_write and not recognized:
        denied = Decision(False, "no recognized action for this request")
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
    """The parts a guard supplies to handle.

    name is the audit guard value (bare strings: "git"/"api"). A guard hook
    does I/O (parse/enrich/record/forward/deny_response) or is pure
    default-deny logic (decide) only — criticality/enablement checks
    are kernel-owned, not per-guard hooks.
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
    def recognizers(self) -> tuple[Recognizer[IntentT], ...]:
        """This guard's recognizer table, most specific row first.

        The kernel calls through it via first_recognized right after parse
        to get the intent's recognized action set.
        """
        ...

    @property
    @abstractmethod
    def supported_actions(self) -> frozenset[Action]:
        """The action set this guard is capable of gating.

        The static ceiling — never what a deployment has actually enabled
        (Config.effective_actions).
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

        The guard owns its own paths so app.py stays generic assembly
        instead of hand-listing every guard's endpoints.
        """
        ...

    def project_allowed(self, host: str, project: str) -> bool:
        """Resource allowlist membership hook. Default: the per-host config
        allowlist (cfg.git_project_allowed). A guard whose forge resolves
        numeric-id aliases (e.g. ApiGuard) overrides this to also accept those.
        """
        return self.cfg.git_project_allowed(host, project)

    def state_view(self, host: str) -> StateView:
        """Quota snapshot hook. Default: this guard's own core-only view (no
        domain state), locked until this guard reconciled. A guard backed by
        a domain overrides this to return the combined snapshot instead.
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

        Uses only resource-agnostic collaborators (cfg/state/audit),
        never a guard's own I/O clients, which stay encapsulated in the subclass.
        """
        correlation_id = str(uuid.uuid4())
        started = time.monotonic()

        intent = await self.parse(request)
        view = self.state_view(intent.host)

        recognized = first_recognized(self.recognizers, intent) or frozenset[Action]()

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
