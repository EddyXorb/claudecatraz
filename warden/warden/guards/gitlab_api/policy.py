"""REST guard policy: pure decide for GitLab API requests.

Knows GitLab REST concepts (MR, endpoint catalog) on purpose. Mode gate (R0) and
resource allowlist (R6) are now the kernel's job; capability invariant (R4) is
exposed here as :func:`capability_gate` for the kernel, not re-checked in :func:`decide`.

§07 Punkt 7: read and write endpoints are both :class:`~.catalog.model.Recognizer`
rows now; :func:`decide` matches one (read table or write catalog, depending on
method) and hands it to :func:`decide_scope` — the **one** generic function that
consumes a recognizer's ``⟨capabilities, scope⟩`` and produces the terminal
Decision. No per-entry decision logic lives outside it.

Rules enforced:
  R1  Read pass-through  GET/HEAD/OPTIONS upstream with READ token; reads are
      "content, not visibility".
  R2  Branch namespace    a literal branch field (source_branch/ref/branch) must
      lie in the agent's configured namespace.
  R3  API write filter   allowlisted write endpoints; MR access resolved via an
      iid → MR upstream lookup (branch-namespace scope, no literal field).
  R4  Irreversible verbs merge never permitted (caught by capability_gate first).
  R5  Quota & rate       max open MRs, max writes/hour; locked state denies (fail-safe).
  R6  Project boundary   applies to projectless-reads too (content-capable or unlisted).

Rule ids are :mod:`core.rules` constants, never bare literals.
"""

from __future__ import annotations

from typing import Callable, Optional

from ...core.capabilities import forbidden_check
from ...core.config import Config
from ...core.guard import kernel_gates
from ...core.model import Decision, StateView, TokenKind
from ...core.rules import R1, R2, R3, R4, R5, R6
from .catalog import (
    EffectiveTable,
    EndpointKind,
    Recognizer,
    ScopeKind,
    api_capabilities,
    build_effective_table,
    is_builtin_merge_endpoint,
    match_endpoint,
)
from .catalog.model import ReadClass
from .catalog.read_endpoints import match_read
from .intent import ApiIntent

_READ_METHODS = ("GET", "HEAD", "OPTIONS")


def capability_gate(
    intent: ApiIntent, cfg: Config, effective: EffectiveTable
) -> Optional[Decision]:
    """Kernel hook: check capabilities before guard-specific logic.

    Reads return ``None`` trivially. The merge endpoint's built-in invariant
    (never a catalog row) is checked here, ahead of :func:`decide`'s R3 default.
    """
    if not intent.writes:
        return None
    if is_builtin_merge_endpoint(intent.method, intent.path):
        return Decision(False, R4, "merge is never permitted")
    ep = intent.endpoint or match_endpoint(effective.entries, intent.method, intent.path)
    if ep is None:
        return None  # no matched row ⇒ nothing to map; decide() default-denies R3
    return forbidden_check(api_capabilities(ep, intent.fields))


def decide(intent: ApiIntent, state: StateView, cfg: Config, effective: EffectiveTable) -> Decision:
    """Default-deny guard-specific logic after kernel gates: match a
    recognizer (read table or write catalog, by method) and hand it to
    :func:`decide_scope` — the one generic scope decision.

    A project id in the path already cleared the kernel's resource-allowlist
    gate (:func:`core.guard.project_gate`, run before ``decide`` is ever
    reached) — that gate covers repository content (files, diffs, wiki, …)
    under a project, so a project-bound read is unconditionally allowed
    without ever consulting a recognizer.
    """
    if intent.method.upper() in _READ_METHODS:
        if intent.project:
            return Decision(True, R1, "read pass-through", TokenKind.READ)
        rec = match_read(intent.path)
        if rec is None:
            return Decision(
                False,
                R6,
                f"projectless read endpoint not in allowlist: {intent.method} {intent.path}",
            )
        return decide_scope(intent, rec, state, cfg)

    # Write methods: endpoint must be in the effective table (Catalog × config),
    # default-deny otherwise. Merge endpoint never reaches here (capability_gate denies first).
    ep = intent.endpoint or match_endpoint(effective.entries, intent.method, intent.path)
    if ep is None:
        return Decision(
            False, R3, f"write endpoint not in allowlist: {intent.method} {intent.path}"
        )
    return decide_scope(intent, ep, state, cfg)


def decide_scope(intent: ApiIntent, match: Recognizer, state: StateView, cfg: Config) -> Decision:
    """The one generic decision every matched :class:`Recognizer` feeds
    through (§07 Punkt 7) — dispatches purely on ``match.scope_kind``, never
    on the entry's identity.

    * ``CONTENT_EXPOSURE``: terminal — the recognizer's ``classify`` decides
      metadata (R1) vs. content (R6); no state/quota involved.
    * ``BRANCH_NAMESPACE``: a namespace check (literal field or, for the
      iid-lookup rows, the tristate ``intent.mr_source_ok`` populated by
      ``enrich()``) must pass before quota is even considered.
    * ``QUOTA_BY_KIND``: no namespace check at all — falls straight through
      to quota.

    Both write scopes end the same way: R5 quota, then allow with the
    recognizer's own ``rule``.
    """
    if match.scope_kind is ScopeKind.CONTENT_EXPOSURE:
        assert match.classify is not None, f"{match.id!r} is content-exposure with no classifier"
        read_class, reason = match.classify(intent)
        if read_class is ReadClass.METADATA:
            return Decision(True, R1, reason, TokenKind.READ)
        return Decision(False, R6, reason)

    if match.scope_kind is ScopeKind.BRANCH_NAMESPACE:
        denied = _branch_namespace_check(intent, match, cfg)
        if denied is not None:
            return denied

    rules = cfg.effective_rules(intent.host)  # step 04: per-endpoint quota ceiling
    max_open_mrs, max_writes_per_hour = rules.max_open_mrs, rules.max_writes_per_hour
    assert max_open_mrs is not None and max_writes_per_hour is not None, (
        "effective_rules always resolves every field to a concrete built-in default"
    )
    quota = _quota_check(match, state, max_open_mrs, max_writes_per_hour)
    if quota is not None:
        return quota
    return Decision(True, match.rule, "ok", TokenKind.WRITE)


def _branch_namespace_check(
    intent: ApiIntent, match: Recognizer, cfg: Config
) -> Optional[Decision]:
    """R2/R3 for a ``BRANCH_NAMESPACE`` recognizer.

    ``namespace_field`` set: the branch is literally in the request (body or
    query, per ``decision_fields``) — a mismatch is R2 (own-namespace
    violation, the caller's own request is the witness).

    ``namespace_field`` is ``None``: the request carries only an iid: the
    branch was resolved via the iid → MR upstream lookup, in
    ``intent.mr_source_ok`` (tristate: ``True``/``False``/unverifiable
    ``None``, populated by the guard's ``enrich()``) — a mismatch or
    unverifiable lookup is R3.
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
    ep: Recognizer, state: StateView, max_open_mrs: int, max_writes_per_hour: int
) -> Optional[Decision]:
    """``max_open_mrs``/``max_writes_per_hour`` are the endpoint's own resolved
    ceilings (``Config.effective_rules(intent.host)``, step 04) — see
    ``guards.git.policy.check_ref``'s docstring for why these are plain ints,
    never a global ``Config`` field nor a raw (``Optional``-fielded)
    ``GitRules``."""
    if state.locked:  # Fail-safe: never "empty = free"
        return Decision(False, R5, "state locked (fail-safe) — reconcile pending")
    if state.writes_last_hour >= max_writes_per_hour:
        return Decision(False, R5, f"rate limit reached ({max_writes_per_hour}/h)")
    if ep.kind == EndpointKind.MR and state.open_mrs >= max_open_mrs:
        return Decision(False, R5, f"max open MRs reached ({max_open_mrs})")
    return None


def full_decide(
    intent: ApiIntent,
    state: StateView,
    cfg: Config,
    effective: Optional[EffectiveTable] = None,
    project_allowed: Optional[Callable[[str], bool]] = None,
) -> Decision:
    """Compose kernel gates with guard-specific decide for callers outside Guard.handle.

    Used by tests exercising the whole effective decision, not just this module's slice.

    ``effective`` and ``project_allowed`` default to the real values for free.
    """
    if effective is None:
        effective = build_effective_table(cfg.effective_actions(intent.host))
    d = kernel_gates(intent, cfg, project_allowed or cfg.project_allowed)
    if d is None:
        d = capability_gate(intent, cfg, effective)
    if d is None:
        d = decide(intent, state, cfg, effective)
    return d
