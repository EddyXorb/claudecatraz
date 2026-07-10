"""git guard policy: pure, per-ref rules for a receive-pack push.

Branch namespace and quotas are the only checks left here — irreversible
verbs (tag push, branch/tag delete) are denied by the recognized action's
criticality, before any of this runs. The write-credential gate and the
project resource allowlist are the kernel's job.

Checks enforced here:
  git write limits    push only to branches under allowed <branch_prefix>.
  Irreversible verb    a recognized IRREVERSIBLE action is never enabled.
  Quota & rate         max open branches, max writes/hour, max push size;
                       locked state denies (fail-safe).
  Action allowlist     the recognized action must be in the host's
                       effective actions — the same boundary the kernel's
                       project allowlist enforces, both instances of
                       "resource/action outside the configured boundary."
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

from ....core.actions import Action, Criticality
from ....core.config import Config
from ....core.guard import kernel_gates
from ....core.model import Decision, StateView, TokenKind
from . import recognizers
from .intent import GitIntent
from .pktline import RefCommand


def _action_decision(actions: frozenset[Action], host: str, cfg: Config) -> Optional[Decision]:
    """Deny if any recognized action is irreversible or missing from the
    host's effective actions. An empty actions set — an unrecognized
    ref shape — denies fail-closed too.
    """
    if not actions:
        return Decision(False, "no recognized action for this request")
    for action in sorted(actions, key=lambda a: a.id):
        if action.criticality is Criticality.IRREVERSIBLE:
            return Decision(False, f"action {action.id} is irreversible, never permitted")
        if action.id not in cfg.effective_actions(host):
            return Decision(False, f"action {action.id} not enabled for host {host!r}")
    return None


def ref_action_gate(cmd: RefCommand, host: str, cfg: Config) -> Optional[Decision]:
    """The criticality/membership gate for one ref-command, used both by
    action_gate (batch-atomic) and by the guard's per-ref deny
    response (each ref names its own denied action).
    """
    return _action_decision(recognizers.ref_command_action(cmd), host, cfg)


def action_gate(intent: GitIntent, cfg: Config) -> Optional[Decision]:
    """Deny a git-transport operation whose recognized action(s) are missing
    from the host's effective actions, or are irreversible.

    Runs for all three operations — crucially advertise — so a
    repo.read-disabled host is denied already at push/fetch discovery,
    before the client ever sends a pack. For receive-pack, every
    ref-command is checked; the first denial rejects the whole batch,
    matching decide's per-ref quota atomicity.

    Relies on the kernel's host gate having already run and passed for
    intent.host: a host with no [[git.endpoint]] entry is denied
    there first. This matters because effective_actions cannot itself
    distinguish "no endpoint" from "endpoint inheriting the domain/built-in
    default" — both return the same non-empty default — so this gate must
    never be the first thing to see an unconfigured host.
    """
    if intent.operation != "receive-pack":
        return _action_decision(recognizers.recognize(intent), intent.host, cfg)
    for cmd in intent.ref_commands:
        denied = ref_action_gate(cmd, intent.host, cfg)
        if denied is not None:
            return denied
    return None


def check_ref(
    cmd: RefCommand, state: StateView, cfg: Config, max_open_branches: int, max_writes_per_hour: int
) -> Decision:
    """Branch-namespace and quota checks for one ref-command already cleared
    by action_gate — a tag or a delete never reaches here, both denied
    earlier as irreversible.

    max_open_branches/max_writes_per_hour are the endpoint's own
    resolved ceilings (Config.effective_rules(intent.host)) —
    stateful quotas are per-endpoint, never a global Config field, so the
    caller resolves the cascade once per request and passes the concrete
    ints through (GitRules' fields are Optional — sentinels for the
    cascade merge itself — so the caller, not this function, is where the
    None-after-cascade invariant is asserted).
    """
    ref = cmd.ref.removeprefix("refs/heads/")
    if not cfg.in_branch_namespace(ref):  # branch namespace
        return Decision(False, f"branch {ref!r} outside allowed prefixes {cfg.branch_prefixes!r}")
    if state.locked:  # Fail-safe
        return Decision(False, "state locked (fail-safe) — reconcile pending")
    if cmd.is_create and state.open_branches >= max_open_branches:  # quota
        return Decision(False, f"max open branches reached ({max_open_branches})")
    if state.writes_last_hour >= max_writes_per_hour:  # rate
        return Decision(False, f"rate limit reached ({max_writes_per_hour}/h)")
    return Decision(True, "ok", TokenKind.WRITE)


def decide(intent: GitIntent, state: StateView, cfg: Config) -> Decision:
    """Per ref-command: prefix / create-count / rate.

    Atomic: a single forbidden command rejects the whole push. Quotas are accounted
    within the batch — N creates against max_open_branches - 1 must reject.
    """
    if not intent.ref_commands:
        return Decision(False, "no ref commands in push")
    if intent.push_bytes is not None and intent.push_bytes > cfg.max_push_bytes:
        return Decision(
            False,
            f"push body ({intent.push_bytes} bytes) exceeds max_push_bytes ({cfg.max_push_bytes})",
        )
    rules = cfg.effective_rules(intent.host)
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
    return Decision(True, "ok", TokenKind.WRITE)


def full_decide(
    intent: GitIntent,
    state: StateView,
    cfg: Config,
    project_allowed: Optional[Callable[[str], bool]] = None,
) -> Decision:
    """Compose the kernel gates with this guard's pure decide for callers
    outside Guard.handle (tests, and any offline "what would happen to
    this push" evaluator) that need the whole effective decision, not just
    this module's slice.
    """
    recognized = recognizers.recognize(intent)
    d = kernel_gates(intent, cfg, project_allowed or cfg.project_allowed, recognized)
    if d is None:
        d = decide(intent, state, cfg)
    return d
