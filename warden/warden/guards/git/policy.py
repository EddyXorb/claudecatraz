"""git guard policy (§03.2/03.3): pure, per-ref rules for a receive-pack push.

Split out of the old channel-union ``policy.py`` (§06-migration.md Schritt 5,
F1/F3) — this module knows nothing about REST/GitLab endpoints, only git
ref-update semantics, matching §03.3's "git-Protokoll-Guard (generisch)" split.
The mode gate (R0), resource allowlist (R6) and capability invariants (R4,
§03.4) that used to live in the channel-union ``policy.decide`` are now the
kernel's job (:mod:`warden.core.guard`) — everything below is what remains
genuinely git-specific: branch namespace, delete/tag defense-in-depth, quotas.

Rules enforced — every :class:`~warden.core.model.Decision` here is tagged
with one of these for the audit log:

  R2  git write limits   push only to branches under an allowed <branch_prefix>
                         (the namespace is the union of ``branch_prefixes``).
  R4  Irreversible verbs pushing a tag or deleting a branch is never permitted
                         (B3: these used to log as R2 — they are "never"
                         capabilities, not namespace checks, see ``core.rules.R4``).
                         Caught structurally by the kernel's capability gate
                         (:func:`capability_gate` below) *and* kept here as
                         defense-in-depth (A10).
  R5  Quota & rate       max open branches, max writes/hour; a locked
                         (unreconciled) state denies (fail-safe, §6.11).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from ...core.capabilities import Capability, forbidden_check
from ...core.config import Config
from ...core.guard import kernel_gates
from ...core.model import Decision, StateView, TokenKind
from ...core.rules import R2, R4, R5
from .intent import GitPushIntent
from .pktline import RefCommand


def git_ref_capabilities(cmd: RefCommand, cfg: Config) -> frozenset[Capability]:
    """Map one git ref-command to capabilities — trivial and exact (§03.4).

    Mirrors, but does not replace, the special cases in :func:`check_ref`
    (kept as defense-in-depth, A10): this mapping alone must be enough to
    trigger :func:`~warden.core.capabilities.forbidden_check`, independent of
    ``check_ref``'s own logic.

    * A delete (``new`` is all-zero) is ``deletes_ref`` regardless of ref
      type — a tag delete is a delete, not additionally a tag *creation*.
    * A non-deleting push to ``refs/tags/*`` is ``creates_tag``.
    * Anything else is a branch write: ``creates_ref`` when it creates a new
      branch, plus ``writes_outside_namespace`` when the (heads-prefix
      stripped) ref name is outside ``cfg.branch_prefixes`` (M2) — not
      forbidden by itself (see ``core.capabilities.FORBIDDEN``'s docstring),
      but part of the shared vocabulary so a future consumer (e.g. an audit
      report) can ask "did this write leave the namespace" without
      re-deriving it.
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


def capability_gate(intent: GitPushIntent, cfg: Config) -> Optional[Decision]:
    """§03.4 kernel hook (``core.guard.Guard.capability_gate``).

    Checked per ref command, first hit denies — atomic (Q10): a single
    forbidden command denies the whole push, matching :func:`decide`'s own
    batch atomicity (and matching the pre-Schritt-5 per-command loop in
    ``policy._decide_git``, including its deny reason naming only the
    triggering command's capabilities).
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
    if state.locked:  # §6.11 fail-safe
        return Decision(False, R5, "state locked (fail-safe) — reconcile pending")
    if cmd.is_create and state.open_branches >= cfg.max_open_branches:  # R5
        return Decision(False, R5, f"max open branches reached ({cfg.max_open_branches})")
    if state.writes_last_hour >= cfg.max_writes_per_hour:  # R5
        return Decision(False, R5, f"rate limit reached ({cfg.max_writes_per_hour}/h)")
    return Decision(True, R2, "ok", TokenKind.WRITE)


def decide(intent: GitPushIntent, state: StateView, cfg: Config) -> Decision:
    """Per ref-command: prefix / delete / create-count / rate (W7.2).

    Atomic: a single forbidden command rejects the whole push (Q10). Quotas are
    accounted *within* the batch — the commands in one push are not free of
    each other, so N creates against ``max_open_branches - 1`` must reject.
    Mode/project/capability gates already ran in the kernel by the time this
    is reached (§03.2) — this only holds what is genuinely git-specific.
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


def full_decide(intent: GitPushIntent, state: StateView, cfg: Config) -> Decision:
    """Compose the kernel gates with this guard's pure ``decide`` for callers
    outside :meth:`core.guard.Guard.handle` (tests, and any offline "what would
    happen to this push" evaluator) that need the *whole* effective decision,
    not just this module's slice — mirrors
    ``guards.gitlab_api.policy.full_decide``.
    """
    d = kernel_gates(intent, cfg)
    if d is None:
        d = capability_gate(intent, cfg)
    if d is None:
        d = decide(intent, state, cfg)
    return d
