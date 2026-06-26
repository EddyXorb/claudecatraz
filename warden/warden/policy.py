"""The policy core (W5): pure, no I/O, table-driven, default-deny.

`decide(req, state, cfg)` is deterministic — it sees an already-parsed intent
(:class:`ProxyRequest`) and a snapshot of the counters (:class:`StateView`),
never the network. That is what makes it directly unit-testable (§8.1) and the
basis of the audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

from .allowlist import WriteEndpoint, match_endpoint
from .config import Config
from .pktline import RefCommand


class TokenKind(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    NONE = "NONE"


@dataclass(frozen=True)
class Decision:
    allow: bool
    rule: str  # "R1".."R6" — for the audit log
    reason: str
    token: TokenKind = TokenKind.NONE  # which upstream token, if allow


@dataclass(frozen=True)
class StateView:
    """Snapshot of the quota counters (W5). ``locked`` ⇒ fail-safe deny (§6.11)."""

    open_mrs: int = 0
    open_branches: int = 0
    writes_last_hour: int = 0
    locked: bool = False


@dataclass
class ProxyRequest:
    """The parsed intent handed to :func:`decide` — no transport state."""

    channel: str  # 'api' | 'git'
    project: str
    method: str = ""
    path: str = ""  # REST path after /api/v4, e.g. /projects/123/merge_requests
    endpoint: Optional[WriteEndpoint] = None  # matched write endpoint (api)
    fields: dict = field(default_factory=dict)  # extracted body/query fields
    ref_commands: list[RefCommand] = field(default_factory=list)  # git push
    # Resolved by api_proxy via an upstream lookup (W6.2); None ⇒ unverifiable.
    mr_owner_ok: Optional[bool] = None


# --- pure check predicates (W6) -------------------------------------------------
# Each returns (ok, rule, reason). Names are referenced from allowlist.WRITE_ENDPOINTS.


def _src_branch_prefix(req: ProxyRequest, state: StateView, cfg: Config):
    src = req.fields.get("source_branch", "")
    if src.startswith(cfg.branch_prefix):
        return True, "R3", "ok"
    return False, "R2", f"source_branch {src!r} without prefix {cfg.branch_prefix!r}"


def _ref_prefix(req: ProxyRequest, state: StateView, cfg: Config):
    ref = req.fields.get("ref", "")
    if ref.startswith(cfg.branch_prefix):
        return True, "R3", "ok"
    return False, "R2", f"ref {ref!r} without prefix {cfg.branch_prefix!r}"


def _mr_owned_by_claude(req: ProxyRequest, state: StateView, cfg: Config):
    if req.mr_owner_ok is True:
        return True, "R3", "ok"
    if req.mr_owner_ok is None:
        return False, "R3", "MR ownership could not be verified"
    return False, "R3", "MR not owned by the service account"


def _not_merge_intent(req: ProxyRequest, state: StateView, cfg: Config):
    if req.fields.get("state_event") == "merge":
        return False, "R4", "state_event=merge is a merge alias"
    return True, "R3", "ok"


def _always_deny(req: ProxyRequest, state: StateView, cfg: Config):
    return False, "R4", "merge is never permitted"


_CHECKS = {
    "src_branch_prefix": _src_branch_prefix,
    "ref_prefix": _ref_prefix,
    "mr_owned_by_claude": _mr_owned_by_claude,
    "not_merge_intent": _not_merge_intent,
    "ALWAYS_DENY": _always_deny,
}


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
    if req.channel == "git":
        # git always targets a concrete project — it must be allowlisted (R6).
        denied = project_gate(req.project, cfg)
        if denied is not None:
            return denied
        return _decide_git(req, state, cfg)
    if req.channel == "api":
        # Project allowlist applies where a project id appears in the path (W6.4).
        denied = project_gate(req.project, cfg)
        if denied is not None:
            return denied
        return _decide_api(req, state, cfg)
    return Decision(False, "R6", f"unknown channel {req.channel!r}")


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
    if not ref.startswith(cfg.branch_prefix):  # R2
        return Decision(False, "R2", f"branch {ref!r} without prefix {cfg.branch_prefix!r}")
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

    for name in ep.checks:
        ok, rule, reason = _CHECKS[name](req, state, cfg)
        if not ok:
            return Decision(False, rule, reason)

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
    if ep.kind == "mr" and state.open_mrs >= cfg.max_open_mrs:
        return Decision(False, "R5", f"max open MRs reached ({cfg.max_open_mrs})")
    return None
