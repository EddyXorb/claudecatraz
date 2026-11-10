"""GitHub REST guard policy: pure decide over the recognizer catalog.

The kernel denies criticality/enablement violations before decide runs;
decide handles what remains — read pass-through, or a write's scope and
quota checks (state.locked always denies)."""

from __future__ import annotations

from typing import Optional

from ....core.actions import Action
from ....core.config import Config
from ....core.model import Decision, StateView, TokenKind
from .. import actions as git_actions
from .intent import GithubIntent
from .recognizers import GithubRecognizer, Scope, match_request


def recognize(intent: GithubIntent) -> tuple[Optional[GithubRecognizer], frozenset[Action]]:
    """(matching row, recognized actions); an empty set always denies."""
    match = match_request(intent)
    if match is None:
        return None, frozenset()
    return match, match(intent) or frozenset()


def decide(intent: GithubIntent, state: StateView, cfg: Config) -> Decision:
    if intent.is_graphql:
        return Decision(False, "GraphQL is not permitted — unmodelled channel")
    match, recognized = recognize(intent)
    if not recognized:
        kind = "write" if intent.needs_write else "read"
        return Decision(False, f"{kind} endpoint not in allowlist: {intent.method} {intent.path}")
    assert match is not None  # non-empty recognized implies a match
    if not intent.needs_write:
        return Decision(True, "read pass-through", TokenKind.READ)
    denied = _scope_check(intent, match, cfg)
    if denied is None:
        denied = _quota_check(intent, recognized, state, cfg)
    return denied or Decision(True, "ok", TokenKind.WRITE)


_UNVERIFIED = {
    Scope.PULL: "pull request",
    Scope.RUN: "workflow run",
    Scope.JOB: "workflow job",
}


def _scope_check(intent: GithubIntent, match: GithubRecognizer, cfg: Config) -> Optional[Decision]:
    scope = match.effective_scope(intent)
    if scope is Scope.BRANCH_FIELD:
        assert match.namespace_fn is not None, "a BRANCH_FIELD row names its branch"
        branch = match.namespace_fn(intent)
        if branch and cfg.in_branch_namespace(intent.host, branch):
            return None
        prefixes = cfg.effective_rules(intent.host).branch_prefixes
        return Decision(False, f"branch {branch!r} outside allowed prefixes {prefixes!r}")
    if scope in _UNVERIFIED:
        what = _UNVERIFIED[scope]
        if intent.scope_ok is True:
            return None
        if intent.scope_ok is None:
            return Decision(False, f"{what} could not be verified")
        return Decision(False, f"{what} is not on an agent branch of this repository")
    return None


def _quota_check(
    intent: GithubIntent, recognized: frozenset[Action], state: StateView, cfg: Config
) -> Optional[Decision]:
    """The open-MR ceiling applies to creating a pull request and to
    reopening one — otherwise close-then-reopen would sidestep it."""
    rules = cfg.effective_rules(intent.host)
    max_open_mrs, max_writes_per_hour = rules.max_open_mrs, rules.max_writes_per_hour
    assert max_open_mrs is not None and max_writes_per_hour is not None, (
        "effective_rules always resolves every field to a concrete built-in default"
    )
    if state.locked:
        return Decision(False, "state locked (fail-safe) — reconcile pending")
    if state.writes_last_hour >= max_writes_per_hour:
        return Decision(False, f"rate limit reached ({max_writes_per_hour}/h)")
    opens_pull = git_actions.PROJECT_MR_CREATE in recognized or (
        git_actions.PROJECT_MR_CLOSE in recognized and intent.fields.get("state") == "open"
    )
    if opens_pull and state.open_mrs >= max_open_mrs:
        return Decision(False, f"max open MRs reached ({max_open_mrs})")
    return None
