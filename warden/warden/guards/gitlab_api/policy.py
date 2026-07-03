"""REST guard policy: pure decide for GitLab API requests.

Knows GitLab REST concepts (MR, endpoint catalog) on purpose. Mode gate (R0) and
resource allowlist (R6) are now the kernel's job; capability invariant (R4) is
exposed here as :func:`capability_gate` for the kernel, not re-checked in :func:`decide`.

Rules enforced:
  R1  Read pass-through  GET/HEAD/OPTIONS upstream with READ token; reads are "content, not visibility".
  R3  API write filter   allowlisted write endpoints with ownership checks.
  R4  Irreversible verbs merge never permitted (caught by capability_gate first).
  R5  Quota & rate       max open MRs, max writes/hour; locked state denies (fail-safe).
  R6  Project boundary   applies to projectless-reads too (content-capable or unlisted).

Rule ids are :mod:`core.rules` constants, never bare literals.
"""

from __future__ import annotations

from typing import Callable, Optional

from ...core.capabilities import forbidden_check
from ...core.config import Config
from ...core.guard import kernel_gates
from ...core.model import Decision, StateView, TokenKind
from ...core.rules import R1, R3, R4, R5, R6
from .catalog import (
    CatalogEntry,
    EffectiveTable,
    EndpointKind,
    api_capabilities,
    build_effective_table,
    is_builtin_merge_endpoint,
    match_endpoint,
)
from .intent import ApiIntent
from .read_endpoints import match_read


def capability_gate(
    intent: ApiIntent, cfg: Config, effective: EffectiveTable
) -> Optional[Decision]:
    """Kernel hook: check capabilities before guard-specific logic.

    Reads return ``None`` trivially. The merge endpoint's built-in invariant
    (never a catalog row) is checked here, ahead of :func:`decide`'s R3 default.
    """
    if not intent.writes:
        return None
    if is_builtin_merge_endpoint(intent.method, intent.path):
        return Decision(False, R4, "merge is never permitted")
    ep = intent.endpoint or match_endpoint(effective.entries, intent.method, intent.path)
    if ep is None:
        return None  # no matched row ⇒ nothing to map; decide() default-denies R3
    return forbidden_check(api_capabilities(ep, intent.fields))


def _decide_read(intent: ApiIntent) -> Decision:
    """R1/R6 for REST reads (B1): project-bound paths pass, projectless paths
    are looked up in the read-endpoint table.

    A project id in the path already cleared the kernel's resource-allowlist
    gate (:func:`core.guard.project_gate`, run before ``decide`` is ever
    reached) — that gate covers repository content (files, diffs, wiki, …)
    under a project, so a project-bound read is unconditionally allowed here,
    exactly as before B1. A *projectless* path (no project id for R6 to gate)
    is matched against ``read_endpoints`` — metadata passes (R1),
    content-capable or unlisted paths are denied (R6).
    """
    if intent.project:
        return Decision(True, R1, "read pass-through", TokenKind.READ)
    ep = match_read(intent.path)
    if ep is None:
        return Decision(
            False, R6, f"projectless read endpoint not in allowlist: {intent.method} {intent.path}"
        )
    return ep.decide(intent)


def decide(intent: ApiIntent, state: StateView, cfg: Config, effective: EffectiveTable) -> Decision:
    """Default-deny guard-specific logic after kernel gates (reads vs. allowlist, ownership, quotas)."""
    if intent.method.upper() in ("GET", "HEAD", "OPTIONS"):
        return _decide_read(intent)

    # Write methods: endpoint must be in the effective table (Catalog × config),
    # default-deny otherwise. Merge endpoint never reaches here (capability_gate denies first).
    ep = intent.endpoint or match_endpoint(effective.entries, intent.method, intent.path)
    if ep is None:
        return Decision(
            False, R3, f"write endpoint not in allowlist: {intent.method} {intent.path}"
        )

    for check in ep.checks:
        denied = check(intent, state, cfg)
        if denied is not None:
            return denied

    # R5 quotas, evaluated only for endpoints that actually write.
    quota = _quota_check(ep, state, cfg)
    if quota is not None:
        return quota

    return Decision(True, ep.rule, "ok", TokenKind.WRITE)


def _quota_check(ep: CatalogEntry, state: StateView, cfg: Config) -> Optional[Decision]:
    if state.locked:  # Fail-safe: never "empty = free"
        return Decision(False, R5, "state locked (fail-safe) — reconcile pending")
    if state.writes_last_hour >= cfg.max_writes_per_hour:
        return Decision(False, R5, f"rate limit reached ({cfg.max_writes_per_hour}/h)")
    if ep.kind == EndpointKind.MR and state.open_mrs >= cfg.max_open_mrs:
        return Decision(False, R5, f"max open MRs reached ({cfg.max_open_mrs})")
    return None


def full_decide(
    intent: ApiIntent,
    state: StateView,
    cfg: Config,
    effective: Optional[EffectiveTable] = None,
    project_allowed: Optional[Callable[[str], bool]] = None,
) -> Decision:
    """Compose kernel gates with guard-specific decide for callers outside Guard.handle.

    For endpoint-catalog startgate, tests: the whole effective decision, not just this
    module's slice. Probes fail against capability invariant, not just guard's own checks.

    ``effective`` and ``project_allowed`` default to the real values for free.
    """
    if effective is None:
        effective = build_effective_table(cfg, cfg.endpoint_enable)
    d = kernel_gates(intent, cfg, project_allowed or cfg.project_allowed)
    if d is None:
        d = capability_gate(intent, cfg, effective)
    if d is None:
        d = decide(intent, state, cfg, effective)
    return d
