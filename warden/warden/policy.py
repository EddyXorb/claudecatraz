"""The policy core (W5): pure, no I/O, table-driven, default-deny.

`decide(req, state, cfg)` is deterministic — it sees an already-parsed intent
(:class:`ProxyRequest`) and a snapshot of the counters (:class:`StateView`),
never the network. That is what makes it directly unit-testable (§8.1) and the
basis of the audit trail.

Rules enforced — every :class:`Decision` is tagged with one of these for the
audit log, so the file is self-contained without re-reading the README:

  R0  Mode gate          GitLab is disabled (GITLAB_MODE=off) — all ops denied;
                         or writes are disabled (GITLAB_MODE=read-only) — write
                         ops denied.  Checked before all other rules.
  R1  Read pass-through  REST GET/HEAD/OPTIONS and git upload-pack/info-refs are
                         streamed upstream with the READ token.
  R2  git write limits   push only to branches under an allowed <branch_prefix>
                         (the namespace is the union of ``branch_prefixes``); no
                         branch deletes; no tag pushes.
  R3  API write filter   only allowlisted write endpoints, with ownership checks
                         (source_branch prefix; MR authored by the service acct).
  R4  Merge block        merging an MR is never permitted (incl. the
                         state_event=merge alias).
  R5  Quota & rate       max open branches/MRs, max writes/hour; a locked
                         (unreconciled) state denies (fail-safe, §6.11).
  R6  Project boundary   the project must be in ALLOWED_PROJECTS; the agent
                         itself holds no GitLab token.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .api_endpoints import EndpointKind, WriteEndpoint, match_endpoint
from .config import Config
from .model import Channel, Decision, ProxyRequest, StateView, TokenKind
from .pktline import RefCommand


def project_gate(project: str, cfg: Config) -> Optional[Decision]:
    """R6 project allowlist — the single source of truth (W6.4).

    Returns a deny :class:`Decision` if ``project`` is set but not allowlisted,
    else ``None`` (gate passed). Shared by :func:`decide` and the git read paths
    so the rule lives in exactly one place. An empty ``project`` passes: API
    routes without a project id in the path are gated elsewhere.
    """
    if project and not cfg.project_allowed(project):
        return Decision(False, "R6", f"project {project!r} not in allowlist")
    return None


def decide(req: ProxyRequest, state: StateView, cfg: Config) -> Decision:
    """Default-deny. Every allow path is explicit (W5)."""
    # R0: deny all ops when GitLab is intentionally disabled.
    if not cfg.gitlab_enabled:
        return Decision(False, "R0", "GitLab disabled (GITLAB_MODE=off)")

    if req.channel == Channel.GIT:
        # git always targets a concrete project — it must be allowlisted (R6).
        denied = project_gate(req.project, cfg)
        if denied is not None:
            return denied
        d = _decide_git(req, state, cfg)
    elif req.channel == Channel.API:
        # Project allowlist applies where a project id appears in the path (W6.4).
        denied = project_gate(req.project, cfg)
        if denied is not None:
            return denied
        d = _decide_api(req, state, cfg)
    else:
        return Decision(False, "R6", f"unknown channel {req.channel!r}")

    # R0: deny write operations when writes are not enabled (read-only mode).
    if d.allow and d.token == TokenKind.WRITE and not cfg.writes_enabled:
        return Decision(False, "R0", f"writes disabled (GITLAB_MODE={cfg.gitlab_mode})")

    return d


def _decide_git(req: ProxyRequest, state: StateView, cfg: Config) -> Decision:
    """Per ref-command: prefix / delete / create-count / rate (W7.2).

    Atomic: a single forbidden command rejects the whole push (Q10). Quotas are
    accounted *within* the batch — the commands in one push are not free of each
    other, so N creates against ``max_open_branches - 1`` must reject.
    """
    if not req.ref_commands:
        return Decision(False, "R2", "no ref commands in push")
    pending_branches = 0
    pending_writes = 0
    for cmd in req.ref_commands:
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
    return Decision(True, "R2", "ok", TokenKind.WRITE)


def check_ref(cmd: RefCommand, state: StateView, cfg: Config) -> Decision:
    if cmd.ref.startswith("refs/tags/"):  # tags are never claude/* branches
        return Decision(False, "R2", "tag pushes are not permitted")
    ref = cmd.ref
    if ref.startswith("refs/heads/"):
        ref = ref[len("refs/heads/") :]
    if not cfg.in_branch_namespace(ref):  # R2
        return Decision(
            False, "R2", f"branch {ref!r} outside allowed prefixes {cfg.branch_prefixes!r}"
        )
    if cmd.is_delete:  # R2: deleting a branch is never allowed (Q3)
        return Decision(False, "R2", f"deleting branch {ref!r} is forbidden")
    if state.locked:  # §6.11 fail-safe
        return Decision(False, "R5", "state locked (fail-safe) — reconcile pending")
    if cmd.is_create and state.open_branches >= cfg.max_open_branches:  # R5
        return Decision(False, "R5", f"max open branches reached ({cfg.max_open_branches})")
    if state.writes_last_hour >= cfg.max_writes_per_hour:  # R5
        return Decision(False, "R5", f"rate limit reached ({cfg.max_writes_per_hour}/h)")
    return Decision(True, "R2", "ok", TokenKind.WRITE)


def _decide_api(req: ProxyRequest, state: StateView, cfg: Config) -> Decision:
    # GET / read → pass-through with READ token (R1).
    if req.method.upper() in ("GET", "HEAD", "OPTIONS"):
        return Decision(True, "R1", "read pass-through", TokenKind.READ)

    # Write methods: endpoint must be in the allowlist (default-deny otherwise).
    ep = req.endpoint or match_endpoint(req.method, req.path)
    if ep is None:
        return Decision(False, "R3", f"write endpoint not in allowlist: {req.method} {req.path}")

    for check in ep.checks:
        denied = check(req, state, cfg)
        if denied is not None:
            return denied

    # R5 quotas, evaluated only for endpoints that actually write.
    quota = _quota_check(ep, state, cfg)
    if quota is not None:
        return quota

    return Decision(True, ep.rule, "ok", TokenKind.WRITE)


def _quota_check(ep: WriteEndpoint, state: StateView, cfg: Config) -> Optional[Decision]:
    if state.locked:  # §6.11 fail-safe: never "empty = free"
        return Decision(False, "R5", "state locked (fail-safe) — reconcile pending")
    if state.writes_last_hour >= cfg.max_writes_per_hour:
        return Decision(False, "R5", f"rate limit reached ({cfg.max_writes_per_hour}/h)")
    if ep.kind == EndpointKind.MR and state.open_mrs >= cfg.max_open_mrs:
        return Decision(False, "R5", f"max open MRs reached ({cfg.max_open_mrs})")
    return None
