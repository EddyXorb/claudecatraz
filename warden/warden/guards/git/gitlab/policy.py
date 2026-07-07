"""GitLab REST guard policy: pure decide over the recognizer catalog.

Every matched request is a set of recognized actions (recognizers.CATALOG).
The kernel's criticality/action gates deny an irreversible action, or one the
host's config doesn't enable, before decide ever runs; decide handles
what is left: read pass-through, or a write's branch-namespace/quota check.

Rules enforced:
  R1  Read pass-through   matched read action allowed with the READ token.
  R2  Branch namespace     a literal branch field (source_branch/ref/branch)
      must lie in the agent's configured namespace.
  R3  API write filter    an unmatched/unrecognized write, or an
      unverifiable iid -> MR namespace lookup, is denied.
  R4  Irreversible verbs  any recognized action at IRREVERSIBLE criticality —
      never permitted, regardless of config (kernel gate).
  R5  Quota & rate        max open MRs, max writes/hour; locked state denies.
  R6  Project boundary    unmatched/not-enabled projectless reads; GraphQL;
      a recognized action not enabled for the host (kernel gate).

Rule ids are core.rules constants, never bare literals.
"""

from __future__ import annotations

from typing import Callable, Optional

from ....core.actions import Action
from ....core.config import Config
from ....core.guard import kernel_gates
from ....core.model import Decision, StateView, TokenKind
from ....core.rules import R1, R2, R3, R5, R6
from . import actions as gitlab_actions
from .intent import ApiIntent
from .recognizers import RestRecognizer, ScopeKind, match_request


def _recognize(intent: ApiIntent) -> tuple[Optional[RestRecognizer], frozenset[Action]]:
    """Match intent against the catalog and recognize its action set.

    Returns (None, frozenset()) for no match, and (match, frozenset())
    for a match whose fields carry no known meaning (fail-closed) — both
    outcomes deny, distinguished only for a clearer denial reason.
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
        return Decision(False, R6, "GraphQL is not permitted — unmodelled channel")

    match, recognized = _recognize(intent)
    if not recognized:
        kind = "write" if intent.writes else "read"
        reason = f"{kind} endpoint not in allowlist: {intent.method} {intent.path}"
        return Decision(False, R3 if intent.writes else R6, reason)
    assert match is not None  # non-empty recognized implies a match

    if not intent.writes:
        return Decision(True, R1, "read pass-through", TokenKind.READ)

    return decide_scope(intent, match, recognized, state, cfg)


def decide_scope(
    intent: ApiIntent,
    match: RestRecognizer,
    recognized: frozenset[Action],
    state: StateView,
    cfg: Config,
) -> Decision:
    """The one generic decision every matched write recognizer feeds through —
    dispatches purely on match.scope_kind.

    BRANCH_NAMESPACE: a namespace check (literal field or, for iid-lookup
    rows, the tristate intent.mr_source_ok populated by enrich()) must
    pass before quota is considered. QUOTA_BY_KIND: no namespace check —
    falls straight through to quota.
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
    return Decision(True, R3, "ok", TokenKind.WRITE)


def _branch_namespace_check(
    intent: ApiIntent, match: RestRecognizer, cfg: Config
) -> Optional[Decision]:
    """R2/R3 for a BRANCH_NAMESPACE recognizer.

    namespace_field set: the branch is literally in the request (body or
    query, per decision_fields) — a mismatch is R2 (the caller's own
    request is the witness). namespace_field is None: the branch was
    resolved via the iid -> MR upstream lookup, in intent.mr_source_ok
    (True/False/unverifiable None) — a mismatch or unverifiable
    lookup is R3.
    """
    if match.namespace_field is not None:
        value = intent.fields.get(match.namespace_field, "")
        if cfg.in_branch_namespace(value):
            return None
        return Decision(
            False,
            R2,
            f"{match.namespace_field} {value!r} outside allowed prefixes {cfg.branch_prefixes!r}",
        )

    if intent.mr_source_ok is True:
        return None
    if intent.mr_source_ok is None:
        return Decision(False, R3, "MR source branch could not be verified")
    return Decision(False, R3, "MR source_branch is outside the allowed branch namespace")


def _quota_check(
    recognized: frozenset[Action], state: StateView, max_open_mrs: int, max_writes_per_hour: int
) -> Optional[Decision]:
    """max_open_mrs/max_writes_per_hour are the endpoint's own resolved
    ceilings (Config.effective_rules(intent.host)). Quota kind is looked up
    from the recognized action (guards.git.gitlab.actions.QUOTA_KIND), not
    declared per row — a write recognizes to exactly one action by the time
    this runs.
    """
    if state.locked:  # Fail-safe: never "empty = free"
        return Decision(False, R5, "state locked (fail-safe) — reconcile pending")
    if state.writes_last_hour >= max_writes_per_hour:
        return Decision(False, R5, f"rate limit reached ({max_writes_per_hour}/h)")
    action = next(iter(recognized))
    if gitlab_actions.QUOTA_KIND.get(action.id) is gitlab_actions.QuotaKind.MR and (
        state.open_mrs >= max_open_mrs
    ):
        return Decision(False, R5, f"max open MRs reached ({max_open_mrs})")
    return None


def full_decide(
    intent: ApiIntent,
    state: StateView,
    cfg: Config,
    effective_actions: Optional[frozenset[str]] = None,
    project_allowed: Optional[Callable[[str], bool]] = None,
) -> Decision:
    """Compose kernel gates with guard-specific decide for callers outside Guard.handle.

    Used by tests exercising the whole effective decision, not just this
    module's slice. effective_actions/project_allowed default to the
    real values for free.
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
