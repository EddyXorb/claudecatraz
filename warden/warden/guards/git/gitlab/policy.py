"""GitLab REST guard policy: pure decide over the recognizer catalog.

The kernel denies criticality/enablement violations before decide runs;
decide handles what remains — read pass-through, or a write's
branch-namespace and quota checks (state.locked always denies)."""

from __future__ import annotations

from typing import Callable, Optional

from ....core.actions import Action
from ....core.config import Config
from ....core.guard import kernel_gates
from ....core.model import Decision, StateView, TokenKind
from .. import actions as git_actions
from .intent import ApiIntent
from .recognizers import RestRecognizer, ScopeKind, match_request


def _recognize(intent: ApiIntent) -> tuple[Optional[RestRecognizer], frozenset[Action]]:
    """Match intent against the catalog and recognize its action set.

    Returns (None, frozenset()) for no match, and (match, frozenset()) for a
    match with no recognized meaning (fail-closed) — both outcomes deny.
    """
    match = match_request(intent)
    if match is None:
        return None, frozenset()
    return match, match(intent) or frozenset()


def decide(
    intent: ApiIntent, state: StateView, cfg: Config, effective_actions: frozenset[str]
) -> Decision:
    """Default-deny guard-specific logic after the kernel gates: a matched,
    recognized, criticality/enablement-cleared request either passes through
    (read) or goes through decide_scope (write).
    """
    if intent.is_graphql:
        return Decision(False, "GraphQL is not permitted — unmodelled channel")

    match, recognized = _recognize(intent)
    if not recognized:
        kind = "write" if intent.needs_write else "read"
        reason = f"{kind} endpoint not in allowlist: {intent.method} {intent.path}"
        return Decision(False, reason)
    assert match is not None  # non-empty recognized implies a match

    if not intent.needs_write:
        return Decision(True, "read pass-through", TokenKind.READ)

    return decide_scope(intent, match, recognized, state, cfg)


def decide_scope(
    intent: ApiIntent,
    match: RestRecognizer,
    recognized: frozenset[Action],
    state: StateView,
    cfg: Config,
) -> Decision:
    """The one generic decision every matched write recognizer feeds through —
    dispatches purely on match.scope_kind. BRANCH_NAMESPACE checks the
    namespace (literal field or intent.mr_source_ok) before quota;
    QUOTA_BY_KIND skips straight to quota.
    """
    if match.scope_kind is ScopeKind.BRANCH_NAMESPACE:
        denied = _branch_namespace_check(intent, match, cfg)
        if denied is not None:
            return denied

    rules = cfg.effective_rules(intent.host)  # per-endpoint quota ceiling
    max_open_mrs, max_writes_per_hour = rules.max_open_mrs, rules.max_writes_per_hour
    assert max_open_mrs is not None and max_writes_per_hour is not None, (
        "effective_rules always resolves every field to a concrete built-in default"
    )
    quota = _quota_check(recognized, state, max_open_mrs, max_writes_per_hour)
    if quota is not None:
        return quota
    return Decision(True, "ok", TokenKind.WRITE)


def _branch_namespace_check(
    intent: ApiIntent, match: RestRecognizer, cfg: Config
) -> Optional[Decision]:
    """Branch-namespace check for a BRANCH_NAMESPACE recognizer.

    namespace_field set: the branch is literal in the request, checked
    directly. namespace_field None: resolved via intent.mr_source_ok;
    a mismatch or unverifiable lookup is denied."""
    if match.namespace_field is not None:
        value = intent.fields.get(match.namespace_field, "")
        if cfg.in_branch_namespace(value):
            return None
        return Decision(
            False,
            f"{match.namespace_field} {value!r} outside allowed prefixes {cfg.branch_prefixes!r}",
        )

    if intent.mr_source_ok is True:
        return None
    if intent.mr_source_ok is None:
        return Decision(False, "MR source branch could not be verified")
    return Decision(False, "MR source_branch is outside the allowed branch namespace")


def _quota_check(
    recognized: frozenset[Action], state: StateView, max_open_mrs: int, max_writes_per_hour: int
) -> Optional[Decision]:
    """max_open_mrs/max_writes_per_hour are the endpoint's own resolved
    ceilings. The open-MR ceiling applies to whichever action carries the
    MR quota kind, read off the recognized action itself.
    """
    if state.locked:  # Fail-safe: never "empty = free"
        return Decision(False, "state locked (fail-safe) — reconcile pending")
    if state.writes_last_hour >= max_writes_per_hour:
        return Decision(False, f"rate limit reached ({max_writes_per_hour}/h)")
    action = next(iter(recognized))
    if action.quota_kind == git_actions.QuotaKind.MR.value and state.open_mrs >= max_open_mrs:
        return Decision(False, f"max open MRs reached ({max_open_mrs})")
    return None


def full_decide(
    intent: ApiIntent,
    state: StateView,
    cfg: Config,
    effective_actions: Optional[frozenset[str]] = None,
    project_allowed: Optional[Callable[[str], bool]] = None,
) -> Decision:
    """Compose kernel gates with guard-specific decide for callers outside Guard.handle.

    Used by tests exercising the whole effective decision. effective_actions
    and project_allowed default to the real values.
    """
    if effective_actions is None:
        effective_actions = frozenset(cfg.effective_actions(intent.host))
    _, recognized = _recognize(intent)
    d = kernel_gates(
        intent, cfg, project_allowed or cfg.project_allowed, recognized, effective_actions
    )
    if d is None:
        d = decide(intent, state, cfg, effective_actions)
    return d
