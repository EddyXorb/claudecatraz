"""Kernel pipeline template method (§03.2, F1; docs/design/architecture-generalization,
§03-guard-architektur.md §03.2, §06-migration.md Schritt 5).

Before this module, ``api_proxy.handle`` and ``git_proxy.receive_pack`` each
built the deny-short-circuit / record-before-forward / audit-on-every-path
sequence by hand (F1). :func:`run_guarded` is the one place that sequence is
built now — a guard supplies the parts (:class:`Guard`), the kernel owns the
order, and a guard cannot reorder or skip a step because it never sees the
sequence, only its own hooks.

Sequence ``run_guarded`` guarantees, in this order:

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
from typing import Any, Mapping, Optional, Protocol, TypeVar

from starlette.requests import Request
from starlette.responses import Response

from .audit import AuditEvent, AuditLog
from .config import Config
from .model import Decision, Intent, StateView
from .rules import R0, R6
from .state import State


def mode_gate_off(cfg: Config) -> Optional[Decision]:
    """M0: deny every operation while GitLab is intentionally disabled.

    Shared by :func:`kernel_gates` and the thin, outside-the-pipeline read
    handlers (git advertise/upload-pack, §03.2) so both call the same
    definition instead of re-testing ``cfg.gitlab_enabled`` inline.
    """
    if not cfg.gitlab_enabled:
        return Decision(False, R0, "GitLab disabled (GITLAB_MODE=off)")
    return None


def mode_gate_writes(cfg: Config) -> Optional[Decision]:
    """M0: deny a write while the deployment is read-only (or off)."""
    if not cfg.writes_enabled:
        return Decision(False, R0, f"writes disabled (GITLAB_MODE={cfg.gitlab_mode})")
    return None


def project_gate(project: str, cfg: Config) -> Optional[Decision]:
    """M6 resource allowlist — the single source of truth, shared by every guard.

    An empty ``project`` passes: an intent that carries no project at all
    (e.g. a projectless REST read) is gated elsewhere, by that guard's own
    ``decide`` (see ``guards.gitlab_api.read_endpoints``) — matching the
    pre-Schritt-5 ``policy.project_gate`` behaviour exactly.
    """
    if project and not cfg.project_allowed(project):
        return Decision(False, R6, f"project {project!r} not in allowlist")
    return None


def kernel_gates(intent: Intent, cfg: Config) -> Optional[Decision]:
    """The guard-agnostic deny gates, in kernel order (module docstring, step 2).

    One definition: :func:`run_guarded` runs this on every pipeline request,
    and each guard's ``full_decide`` composes it with the guard's pure
    ``decide`` so the startgate and unit tests exercise exactly the effective
    order — never a re-derived copy of it.
    """
    denied = mode_gate_off(cfg)
    if denied is None and intent.writes:
        denied = mode_gate_writes(cfg)
    if denied is None:
        denied = project_gate(intent.project, cfg)
    return denied


IntentT = TypeVar("IntentT", bound=Intent)


class Guard(Protocol[IntentT]):
    """The parts a guard supplies to :func:`run_guarded` (§03.2/03.3).

    ``name`` is the audit ``guard`` value (§06-migration.md Schritt 6, F11:
    the JSONL field used to be called ``channel``; the bare string values —
    ``"git"``/``"api"`` — are unchanged). Every method below either does I/O
    (parse/enrich/record/forward/deny_response) or is pure
    (capability_gate/decide) — only the pure half is what §03.4's capability
    invariant and default-deny guarantees rest on.
    """

    @property
    def name(self) -> str: ...

    async def parse(self, request: Request) -> IntentT: ...

    async def enrich(self, intent: IntentT) -> IntentT: ...

    def capability_gate(self, intent: IntentT, cfg: Config) -> Optional[Decision]: ...

    def decide(self, intent: IntentT, state: StateView, cfg: Config) -> Decision: ...

    def record(self, intent: IntentT, decision: Decision) -> None: ...

    async def forward(self, request: Request, intent: IntentT, decision: Decision) -> Response: ...

    def deny_response(self, intent: IntentT, decision: Decision, state: StateView) -> Response: ...

    def audit_fields(self, intent: IntentT) -> Mapping[str, Any]: ...


async def run_guarded(
    guard: Guard[IntentT], request: Request, cfg: Config, state: State, audit: AuditLog
) -> Response:
    """The kernel (§03.2): guarantees the pipeline order regardless of guard.

    ``cfg``/``state``/``audit`` are passed explicitly rather than pulled off a
    guard-specific context object — the kernel only needs the resource-
    agnostic collaborators (M0/M6 gates read ``cfg``; quota fail-safety reads
    ``state``; A7 needs ``audit``), never a guard's own I/O clients
    (upstream credentials, ownership caches, …), which stay encapsulated in
    the guard itself.
    """
    correlation_id = str(uuid.uuid4())
    started = time.monotonic()

    intent = await guard.parse(request)
    view = state.view()

    decision = kernel_gates(intent, cfg)
    if decision is None:
        intent = await guard.enrich(intent)
        decision = guard.capability_gate(intent, cfg)
    if decision is None:
        decision = guard.decide(intent, view, cfg)

    upstream_status: Optional[int]
    if decision.allow:
        guard.record(intent, decision)
        response = await guard.forward(request, intent, decision)
        upstream_status = response.status_code
    else:
        response = guard.deny_response(intent, decision, view)
        upstream_status = None

    audit.log(
        AuditEvent(
            guard=guard.name,
            correlation_id=correlation_id,
            method=intent.method,
            project=intent.project,
            decision=decision,
            state=view,
            started=started,
            upstream_status=upstream_status,
            extra=guard.audit_fields(intent),
        )
    )
    return response
