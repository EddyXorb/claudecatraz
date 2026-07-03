"""git guard policy: pure, per-ref rules for a receive-pack push.

Branch namespace, delete/tag defense-in-depth, quotas — everything genuinely git-specific.
Mode gate (R0), resource allowlist (R6), and capability invariants (R4) are the kernel's job.

Rules enforced:
  R2  git write limits   push only to branches under allowed <branch_prefix>.
  R4  Irreversible verbs tag pushes and branch deletes never permitted.
  R5  Quota & rate       max open branches, max writes/hour; locked state denies (fail-safe).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

from ...core.capabilities import Capability, forbidden_check
from ...core.config import Config
from ...core.guard import kernel_gates
from ...core.model import Decision, StateView, TokenKind
from ...core.rules import R2, R4, R5
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


def capability_gate(intent: GitIntent, cfg: Config) -> Optional[Decision]:
    """Kernel hook: check capabilities per ref command, atomic across push.

    First hit denies the whole push, matching :func:`decide`'s batch atomicity.
    """
    for cmd in intent.ref_commands:
        denied = forbidden_check(git_ref_capabilities(cmd, cfg))
        if denied is not None:
            return denied
    return None


def check_ref(cmd: RefCommand, state: StateView, cfg: Config) -> Decision:
    if cmd.ref.startswith("refs/tags/"):  # tags are never namespace branches
        # B3 fix: an irreversible verb ("never" capability, M4) — R4, not R2.
        return Decision(False, R4, "tag pushes are not permitted")
    ref = cmd.ref
    if ref.startswith("refs/heads/"):
        ref = ref[len("refs/heads/") :]
    if not cfg.in_branch_namespace(ref):  # R2
        return Decision(
            False, R2, f"branch {ref!r} outside allowed prefixes {cfg.branch_prefixes!r}"
        )
    if cmd.is_delete:  # B3 fix: irreversible verb (M4) — R4, not R2 (Q3).
        return Decision(False, R4, f"deleting branch {ref!r} is forbidden")
    if state.locked:  # Fail-safe
        return Decision(False, R5, "state locked (fail-safe) — reconcile pending")
    if cmd.is_create and state.open_branches >= cfg.max_open_branches:  # R5
        return Decision(False, R5, f"max open branches reached ({cfg.max_open_branches})")
    if state.writes_last_hour >= cfg.max_writes_per_hour:  # R5
        return Decision(False, R5, f"rate limit reached ({cfg.max_writes_per_hour}/h)")
    return Decision(True, R2, "ok", TokenKind.WRITE)


def decide(intent: GitIntent, state: StateView, cfg: Config) -> Decision:
    """Per ref-command: prefix / delete / create-count / rate.

    Atomic: a single forbidden command rejects the whole push. Quotas are accounted
    within the batch — N creates against ``max_open_branches - 1`` must reject.
    """
    if not intent.ref_commands:
        return Decision(False, R2, "no ref commands in push")
    pending_branches = 0
    pending_writes = 0
    for cmd in intent.ref_commands:
        view = replace(
            state,
            open_branches=state.open_branches + pending_branches,
            writes_last_hour=state.writes_last_hour + pending_writes,
        )
        d = check_ref(cmd, view, cfg)
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
        d = capability_gate(intent, cfg)
    if d is None:
        d = decide(intent, state, cfg)
    return d
