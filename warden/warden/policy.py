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
                         streamed upstream with the READ token. For REST this is
                         "content, not visibility" (B1): a path with a project id
                         is gated by R6 exactly like a write; a *projectless*
                         path (no project id to gate) is checked against the
                         read-endpoint table (`read_endpoints.py`) — metadata
                         (project/group names, users, …) passes, anything that
                         can return repository content (global/group search
                         with a content `scope`, `/snippets`) is denied, and an
                         unlisted projectless path is denied by default (R6).
  R2  git write limits   push only to branches under an allowed <branch_prefix>
                         (the namespace is the union of ``branch_prefixes``).
  R3  API write filter   only allowlisted write endpoints, with ownership checks
                         (source_branch prefix; MR authored by the service acct).
  R4  Irreversible verbs merging an MR (incl. the state_event=merge alias),
                         pushing a tag, or deleting a branch is never permitted
                         (B3: tag push / branch delete used to log as R2 — they
                         are "never" capabilities like merge, not namespace
                         checks, see ``rules.R4``).
  R5  Quota & rate       max open branches/MRs, max writes/hour; a locked
                         (unreconciled) state denies (fail-safe, §6.11).
  R6  Project boundary   the project must be in ALLOWED_PROJECTS; the agent
                         itself holds no GitLab token. Also used for the two
                         projectless-read denials above (content-capable or
                         unlisted): both are, at bottom, "no allowlisted
                         project backs this read".

Rule ids referenced here are :mod:`rules` constants (B3/F5) — never bare
string literals — so every id in this file traces back to the one registry.

**Capability-invariant layer** (§03.4, B2, ``capabilities.py``): before either
channel's own allow-logic runs, ``_decide_git``/``_decide_api`` derive the
intent's :class:`~warden.capabilities.Capability` set and check it against the
compiled-in ``FORBIDDEN`` set. A hit denies with R4 regardless of what the
channel-specific checks below it would have decided — this is what makes "no
tags / no merges / no branch deletes" a cross-channel property instead of a
git-only line in :func:`check_ref` (B2's actual complaint: the REST channel
did not know about the git-only bans).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .api_endpoints import EndpointKind, WriteEndpoint, api_capabilities, match_endpoint
from .capabilities import forbidden_check, git_ref_capabilities
from .config import Config
from .model import Channel, Decision, ProxyRequest, StateView, TokenKind
from .pktline import RefCommand
from .read_endpoints import match_read
from .rules import R0, R1, R2, R3, R4, R5, R6


def project_gate(project: str, cfg: Config) -> Optional[Decision]:
    """R6 project allowlist — the single source of truth (W6.4).

    Returns a deny :class:`Decision` if ``project`` is set but not allowlisted,
    else ``None`` (gate passed). Shared by :func:`decide` and the git read paths
    so the rule lives in exactly one place. An empty ``project`` passes: API
    routes without a project id in the path are gated elsewhere.
    """
    if project and not cfg.project_allowed(project):
        return Decision(False, R6, f"project {project!r} not in allowlist")
    return None


def decide(req: ProxyRequest, state: StateView, cfg: Config) -> Decision:
    """Default-deny. Every allow path is explicit (W5)."""
    # R0: deny all ops when GitLab is intentionally disabled.
    if not cfg.gitlab_enabled:
        return Decision(False, R0, "GitLab disabled (GITLAB_MODE=off)")

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
        return Decision(False, R6, f"unknown channel {req.channel!r}")

    # R0: deny write operations when writes are not enabled (read-only mode).
    if d.allow and d.token == TokenKind.WRITE and not cfg.writes_enabled:
        return Decision(False, R0, f"writes disabled (GITLAB_MODE={cfg.gitlab_mode})")

    return d


def _decide_git(req: ProxyRequest, state: StateView, cfg: Config) -> Decision:
    """Per ref-command: prefix / delete / create-count / rate (W7.2).

    Atomic: a single forbidden command rejects the whole push (Q10). Quotas are
    accounted *within* the batch — the commands in one push are not free of each
    other, so N creates against ``max_open_branches - 1`` must reject.
    """
    if not req.ref_commands:
        return Decision(False, R2, "no ref commands in push")
    # Capability-invariant layer (§03.4, B2): checked for every command before
    # any allow-logic below runs — a hit here denies regardless of what
    # check_ref would otherwise decide, so the "never" capabilities hold even
    # if check_ref's own tag/delete special cases (kept as defense-in-depth,
    # A10) were ever removed or a new one added without a matching check_ref
    # branch.
    for cmd in req.ref_commands:
        denied = forbidden_check(git_ref_capabilities(cmd, cfg))
        if denied is not None:
            return denied
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
    return Decision(True, R2, "ok", TokenKind.WRITE)


def check_ref(cmd: RefCommand, state: StateView, cfg: Config) -> Decision:
    if cmd.ref.startswith("refs/tags/"):  # tags are never claude/* branches
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


def _decide_read(req: ProxyRequest) -> Decision:
    """R1/R6 for REST reads (B1): project-bound paths pass, projectless paths
    are looked up in the read-endpoint table.

    A project id in the path already cleared :func:`project_gate` (called by
    :func:`decide` before this is reached) — that gate covers repository
    content (files, diffs, wiki, …) under a project, so a project-bound read is
    unconditionally allowed here, exactly as before B1. A *projectless* path
    (no project id for R6 to gate) is matched against ``read_endpoints`` —
    metadata passes (R1), content-capable or unlisted paths are denied (R6).
    """
    if req.project:
        return Decision(True, R1, "read pass-through", TokenKind.READ)
    ep = match_read(req.path)
    if ep is None:
        return Decision(
            False, R6, f"projectless read endpoint not in allowlist: {req.method} {req.path}"
        )
    return ep.decide(req)


def _decide_api(req: ProxyRequest, state: StateView, cfg: Config) -> Decision:
    # GET / read (R1/R6, B1 "content, not visibility"):
    if req.method.upper() in ("GET", "HEAD", "OPTIONS"):
        return _decide_read(req)

    # Write methods: endpoint must be in the allowlist (default-deny otherwise).
    ep = req.endpoint or match_endpoint(req.method, req.path)
    if ep is None:
        return Decision(False, R3, f"write endpoint not in allowlist: {req.method} {req.path}")

    # Capability-invariant layer (§03.4, B2): checked before the endpoint's own
    # checks — a hit here denies even if the matched row's checks (or a future
    # row someone adds without thinking through B2) would otherwise pass it.
    denied = forbidden_check(api_capabilities(ep, req.fields))
    if denied is not None:
        return denied

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
        return Decision(False, R5, "state locked (fail-safe) — reconcile pending")
    if state.writes_last_hour >= cfg.max_writes_per_hour:
        return Decision(False, R5, f"rate limit reached ({cfg.max_writes_per_hour}/h)")
    if ep.kind == EndpointKind.MR and state.open_mrs >= cfg.max_open_mrs:
        return Decision(False, R5, f"max open MRs reached ({cfg.max_open_mrs})")
    return None
