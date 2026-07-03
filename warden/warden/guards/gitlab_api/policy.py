"""REST guard policy (§03.2/03.3): pure decide for GitLab API requests — split
out of the old channel-union ``policy.py`` (§06-migration.md Schritt 5, F1/F3).
Knows GitLab REST concepts (MR, endpoint catalog) on purpose — this is the one
guard §03.3 allows to. The mode gate (R0) and resource allowlist (R6) that
used to live in the channel-union ``policy.decide`` are now the kernel's job
(:mod:`warden.core.guard`); the capability invariant (R4, §03.4) is exposed
here as :func:`capability_gate` for the kernel to call, but is not
re-checked inside :func:`decide` itself (one definition per concept).

Rules enforced — every :class:`~warden.core.model.Decision` here is tagged
with one of these for the audit log:

  R1  Read pass-through  REST GET/HEAD/OPTIONS is streamed upstream with the
                         READ token. This is "content, not visibility" (B1): a
                         path with a project id is gated by R6 (the kernel's
                         resource allowlist) exactly like a write; a
                         *projectless* path (no project id to gate) is
                         checked against the read-endpoint table
                         (``read_endpoints.py``): metadata (project/group
                         names, users, …) passes, anything that can return
                         repository content (global/group search with a
                         content `scope`, `/snippets`) is denied, and an
                         unlisted projectless path is denied by default (R6).
  R3  API write filter   only allowlisted write endpoints, with ownership
                         checks (source_branch prefix; MR authored by the
                         service acct).
  R4  Irreversible verbs merging an MR (incl. the state_event=merge alias) is
                         never permitted — caught by :func:`capability_gate`
                         before this module's own checks ever run.
  R5  Quota & rate       max open MRs, max writes/hour; a locked
                         (unreconciled) state denies (fail-safe, §6.11).
  R6  Project boundary   also used for the two projectless-read denials above
                         (content-capable or unlisted): both are, at bottom,
                         "no allowlisted project backs this read".

Rule ids referenced here are :mod:`core.rules` constants (B3/F5) — never bare
string literals — so every id in this file traces back to the one registry.
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
    """§03.4 kernel hook (``core.guard.Guard.capability_gate``).

    Reads sit here trivially (``intent.writes`` is False, so this returns
    ``None`` immediately — an empty capability set never intersects
    ``FORBIDDEN`` anyway, but skipping the lookup keeps this cheap on the hot
    read path). The merge endpoint's built-in invariant (§04.2/04.3 — never a
    catalog row) is folded in here too, ahead of :func:`decide`'s own R3
    "unknown endpoint" default, exactly like the pre-Schritt-5 channel-union
    ``policy._decide_api`` ordered it.
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
    """Default-deny (W5). Mode/project/capability gates already ran in the
    kernel (§03.2) by the time this is reached — this only holds what is
    genuinely guard-specific: reads vs. the write allowlist, ownership,
    quotas.
    """
    if intent.method.upper() in ("GET", "HEAD", "OPTIONS"):
        return _decide_read(intent)

    # Write methods: endpoint must be in the *effective* table (§04.3) —
    # built once from Catalog × config, never the catalog itself —
    # default-deny otherwise. The merge endpoint never reaches here
    # (capability_gate denies it first); an unmatched write (including a
    # hypothetical bypass of that gate) still default-denies R3.
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
    if state.locked:  # §6.11 fail-safe: never "empty = free"
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
    """Compose the kernel gates with this guard's pure ``decide`` for callers
    outside :meth:`core.guard.Guard.handle` (the endpoint-catalog startgate,
    §04.4; tests) that need the *whole* effective decision, not just this
    module's slice — probes must also fail against the capability invariant,
    not just this guard's own checks, exactly as a real request would.

    ``effective`` defaults to the table built fresh from ``cfg.endpoint_enable``
    so a caller without a live ``ApiGuard`` (the startgate, most tests) still
    gets the real effective table for free. ``project_allowed`` defaults to
    ``cfg.project_allowed`` (the path-form allowlist) — the same default a
    caller without a live ``ApiGuard``/forge (the startgate, most tests) gets
    for free.
    """
    if effective is None:
        effective = build_effective_table(cfg, cfg.endpoint_enable)
    d = kernel_gates(intent, cfg, project_allowed or cfg.project_allowed)
    if d is None:
        d = capability_gate(intent, cfg, effective)
    if d is None:
        d = decide(intent, state, cfg, effective)
    return d
