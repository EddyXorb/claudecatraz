"""git guard policy: pure, per-ref rules for a receive-pack push.

Branch namespace, delete/tag defense-in-depth, quotas — everything genuinely git-specific.
Mode gate (R0) and the project resource allowlist (R6) are the kernel's job.

Rules enforced:
  R2  git write limits   push only to branches under allowed <branch_prefix>.
  R4  Irreversible verbs tag pushes and branch deletes never permitted.
  R5  Quota & rate       max open branches, max writes/hour, max push size;
                         locked state denies (fail-safe).
  R6  Action allowlist   :func:`action_gate`: git.fetch/git.push must be in
                         the host's effective actions — same rule id as the
                         kernel's project allowlist, both instances of the
                         same "resource/action outside the configured
                         boundary" meta-rule (M6).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

from ...core.capabilities import Capability, forbidden_check
from ...core.config import Config
from ...core.guard import kernel_gates
from ...core.model import Decision, StateView, TokenKind
from ...core.rules import R2, R4, R5, R6
from .actions import action_for_git_operation
from .intent import GitIntent
from .pktline import RefCommand


def git_ref_capabilities(cmd: RefCommand, cfg: Config) -> frozenset[Capability]:
    """Map one git ref-command to capabilities — trivial and exact.

    Mirrors the special cases in :func:`check_ref` (defense-in-depth):
    this mapping must be enough to trigger :func:`~warden.core.capabilities.forbidden_check`.

    * Delete (all-zero) is ``deletes_ref`` regardless of ref type.
    * Non-delete push to ``refs/tags/*`` is ``creates_tag``.
    * Branch write: ``creates_ref`` when creating new branch, plus ``writes_outside_namespace``
      when outside ``cfg.branch_prefixes``.
    """
    if cmd.is_delete:
        return frozenset({Capability.DELETES_REF})
    if cmd.ref.startswith("refs/tags/"):
        return frozenset({Capability.CREATES_TAG})
    ref = cmd.ref.removeprefix("refs/heads/")
    caps: set[Capability] = {Capability.CREATES_REF} if cmd.is_create else set()
    if not cfg.in_branch_namespace(ref):
        caps.add(Capability.WRITES_OUTSIDE_NAMESPACE)
    return frozenset(caps)


def action_gate(intent: GitIntent, cfg: Config) -> Optional[Decision]:
    """Deny a git-transport operation whose mapped action
    (:func:`~.actions.action_for_git_operation`) is missing from the host's
    effective actions (:meth:`~warden.core.config.Config.effective_actions`).

    Runs for all three operations — crucially ``advertise`` — so a
    ``git.push``-disabled host is denied already at push discovery, before the
    client ever sends the pack (same shape as the mode-gate/``_writes`` path).
    ``advertise`` carries the requested backend in ``intent.service``;
    :func:`~.actions.action_for_git_operation` reads it to tell fetch-discovery
    from push-discovery apart.

    Relies on :func:`~warden.core.guard.host_gate` (R6) having already run and
    passed for ``intent.host``: a host with no ``[[git.endpoint]]`` entry is
    denied there first. This matters because ``effective_actions`` cannot
    itself distinguish "no endpoint" from "endpoint inheriting the domain/
    built-in default" — both return the same non-empty default — so this gate
    must never be the first thing to see an unconfigured host.
    """
    action = action_for_git_operation(intent.operation, intent.service)
    if action not in cfg.effective_actions(intent.host):
        return Decision(False, R6, f"action {action!r} not enabled for host {intent.host!r}")
    return None


def capability_gate(intent: GitIntent, cfg: Config) -> Optional[Decision]:
    """Kernel hook: check capabilities per ref command, atomic across push.

    First hit denies the whole push, matching :func:`decide`'s batch atomicity.
    """
    for cmd in intent.ref_commands:
        denied = forbidden_check(git_ref_capabilities(cmd, cfg))
        if denied is not None:
            return denied
    return None


def check_ref(
    cmd: RefCommand, state: StateView, cfg: Config, max_open_branches: int, max_writes_per_hour: int
) -> Decision:
    """``max_open_branches``/``max_writes_per_hour`` are the endpoint's own
    resolved ceilings (``Config.effective_rules(intent.host)``) —
    stateful quotas are per-endpoint, never a global ``Config`` field, so the
    caller resolves the cascade once per request and passes the concrete
    ints through (``GitRules``' fields are ``Optional`` — sentinels for the
    cascade merge itself — so the caller, not this function, is where the
    ``None``-after-cascade invariant is asserted).
    """
    if cmd.ref.startswith("refs/tags/"):  # tags are never namespace branches
        # Irreversible verb ("never" capability) — R4, not R2.
        return Decision(False, R4, "tag pushes are not permitted")
    ref = cmd.ref
    if ref.startswith("refs/heads/"):
        ref = ref[len("refs/heads/") :]
    if not cfg.in_branch_namespace(ref):  # R2
        return Decision(
            False, R2, f"branch {ref!r} outside allowed prefixes {cfg.branch_prefixes!r}"
        )
    if cmd.is_delete:  # Irreversible verb — R4, not R2.
        return Decision(False, R4, f"deleting branch {ref!r} is forbidden")
    if state.locked:  # Fail-safe
        return Decision(False, R5, "state locked (fail-safe) — reconcile pending")
    if cmd.is_create and state.open_branches >= max_open_branches:  # R5
        return Decision(False, R5, f"max open branches reached ({max_open_branches})")
    if state.writes_last_hour >= max_writes_per_hour:  # R5
        return Decision(False, R5, f"rate limit reached ({max_writes_per_hour}/h)")
    return Decision(True, R2, "ok", TokenKind.WRITE)


def decide(intent: GitIntent, state: StateView, cfg: Config) -> Decision:
    """Per ref-command: prefix / delete / create-count / rate.

    Atomic: a single forbidden command rejects the whole push. Quotas are accounted
    within the batch — N creates against ``max_open_branches - 1`` must reject.
    """
    if not intent.ref_commands:
        return Decision(False, R2, "no ref commands in push")
    if intent.push_bytes is not None and intent.push_bytes > cfg.max_push_bytes:
        return Decision(
            False,
            R5,
            f"push body ({intent.push_bytes} bytes) exceeds max_push_bytes ({cfg.max_push_bytes})",
        )
    rules = cfg.effective_rules(intent.host)  # step 04: per-endpoint quota ceiling
    max_open_branches, max_writes_per_hour = rules.max_open_branches, rules.max_writes_per_hour
    assert max_open_branches is not None and max_writes_per_hour is not None, (
        "effective_rules always resolves every field to a concrete built-in default"
    )
    pending_branches = 0
    pending_writes = 0
    for cmd in intent.ref_commands:
        view = replace(
            state,
            open_branches=state.open_branches + pending_branches,
            writes_last_hour=state.writes_last_hour + pending_writes,
        )
        d = check_ref(cmd, view, cfg, max_open_branches, max_writes_per_hour)
        if not d.allow:
            return d
        pending_writes += 1
        if cmd.is_create:
            pending_branches += 1
    return Decision(True, R2, "ok", TokenKind.WRITE)


def full_decide(
    intent: GitIntent,
    state: StateView,
    cfg: Config,
    project_allowed: Optional[Callable[[str], bool]] = None,
) -> Decision:
    """Compose the kernel gates with this guard's pure ``decide`` for callers
    outside :meth:`core.guard.Guard.handle` (tests, and any offline "what would
    happen to this push" evaluator) that need the *whole* effective decision,
    not just this module's slice — mirrors
    ``guards.gitlab_api.policy.full_decide``.
    """
    d = kernel_gates(intent, cfg, project_allowed or cfg.project_allowed)
    if d is None:
        d = action_gate(intent, cfg)
    if d is None:
        d = capability_gate(intent, cfg)
    if d is None:
        d = decide(intent, state, cfg)
    return d
