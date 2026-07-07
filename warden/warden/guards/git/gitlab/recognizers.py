"""The GitLab REST recognizer catalog: method + path template -> action set.

CATALOG is matched first-match-wins, most specific row first — the last
row (read.project) is the project-bound catch-all everything else falls
through to. Every row's action set is computed by its own action_fn: a
static set for most rows, a field-conditional lookup for the handful whose
meaning depends on a request field (state_event, search scope) — an
unrecognised field value yields an empty set, which is always a deny
(the fail-closed contract every Recognizer call obeys).
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Optional

from ....core.actions import Action
from ....core.recognizer import Recognizer
from .. import actions as git_actions
from .intent import ApiIntent


class ScopeKind(str, Enum):
    """The two write-scope checks policy.decide_scope performs.

    BRANCH_NAMESPACE: a branch name — literal (namespace_field) or
    resolved via an iid -> MR upstream lookup when namespace_field is
    None — must lie in the agent's configured namespace.
    QUOTA_BY_KIND: no branch concept; only the project boundary (kernel)
    and the writes-per-hour quota apply.
    """

    BRANCH_NAMESPACE = "branch-namespace"
    QUOTA_BY_KIND = "quota-by-kind"


class Location(str, Enum):
    """Where a decision field lives on the wire."""

    BODY = "body"
    QUERY = "query"


@dataclass(frozen=True)
class FieldSpec:
    """One field a recognizer's action depends on, and where to read it.

    The guard extracts only the fields a recognizer declares here, each from
    its declared location — never a blind merge of body and query. A field
    declared BODY that only shows up in the query string is simply absent
    from the decision, exactly as if the caller never sent it.
    """

    name: str
    location: Location = Location.BODY


ActionFn = Callable[[ApiIntent], "frozenset[Action]"]


@dataclass(frozen=True)
class _StaticActions:
    """An ActionFn independent of request fields, exposing the fixed set
    it returns so RestRecognizer.__post_init__ can derive
    possible_actions from it without a second, hand-kept literal.
    """

    actions: frozenset[Action]

    def __call__(self, intent: ApiIntent) -> frozenset[Action]:
        return self.actions


def _static(*actions: Action) -> _StaticActions:
    return _StaticActions(frozenset(actions))


_MR_STATE_ACTIONS: Mapping[object, Action] = {
    None: git_actions.PROJECT_MR_EDIT,
    "close": git_actions.PROJECT_MR_CLOSE,
    "reopen": git_actions.PROJECT_MR_CLOSE,
    "merge": git_actions.PROJECT_MR_MERGE,
}


def _mr_state_event(intent: ApiIntent) -> frozenset[Action]:
    action = _MR_STATE_ACTIONS.get(intent.fields.get("state_event"))
    return frozenset({action}) if action is not None else frozenset()


_ISSUE_STATE_ACTIONS: Mapping[object, Action] = {
    None: git_actions.PROJECT_ISSUE_EDIT,
    "close": git_actions.PROJECT_ISSUE_CLOSE,
    "reopen": git_actions.PROJECT_ISSUE_CLOSE,
}


def _issue_state_event(intent: ApiIntent) -> frozenset[Action]:
    action = _ISSUE_STATE_ACTIONS.get(intent.fields.get("state_event"))
    return frozenset({action}) if action is not None else frozenset()


_SEARCH_SCOPE_ACTIONS: Mapping[object, Action] = {
    "projects": git_actions.INSTANCE_PROJECTS_READ,
    "merge_requests": git_actions.INSTANCE_PROJECTS_READ,
    "issues": git_actions.INSTANCE_PROJECTS_READ,
    "milestones": git_actions.INSTANCE_PROJECTS_READ,
    "users": git_actions.INSTANCE_USERS_READ,
}


def _search_scope(intent: ApiIntent) -> frozenset[Action]:
    action = _SEARCH_SCOPE_ACTIONS.get(intent.fields.get("scope"))
    return frozenset({action}) if action is not None else frozenset()


def _compile(template: str) -> re.Pattern[str]:
    """Compile a REST path template into a fullmatch-ready regex.

    {name} is one URL-encoded path segment. A template ending in the
    literal token {rest} requires one or more further characters after
    that slash (multi-segment, e.g. repository content paths); a template
    ending in {/rest} makes that same tail optional (the bare path alone
    also matches).
    """
    optional_tail = template.endswith("{/rest}")
    if optional_tail:
        template = template[: -len("{/rest}")]

    segments = []
    for seg in template.split("/"):
        if seg == "{rest}":
            segments.append(".+")
        elif seg.startswith("{") and seg.endswith("}"):
            segments.append("[^/]+")
        else:
            segments.append(re.escape(seg))
    pattern = "/".join(segments)
    if optional_tail:
        pattern += "(?:/.+)?"
    return re.compile(pattern)


def _methods(*methods: str) -> frozenset[str]:
    return frozenset(methods)


@dataclass(frozen=True, kw_only=True)
class RestRecognizer(Recognizer[ApiIntent]):
    """One catalog row: method(s) + path template -> action set.

    scope_kind/namespace_field are the scope-policy payload
    policy.decide_scope consumes — meaningful only for write rows; a read
    row leaves them at their defaults. A BRANCH_NAMESPACE row with
    namespace_field=None resolves its branch via the iid -> MR upstream
    lookup (intent.mr_source_ok) instead of a literal field. Quota kind is
    not declared here — it is a function of the recognized action, not of
    the row (guards.git.gitlab.actions.QUOTA_KIND).

    possible_actions (the policy report's static view of this row,
    required by Recognizer) is derived automatically from action_fn
    when it is a _static set — there is nothing to declare twice. A
    field-conditional row must pass possible_actions_override instead
    (the full range of its lookup table), enforced in __post_init__ so a
    new field-conditional row can't silently ship without one.
    """

    id: str
    methods: frozenset[str]
    template: str
    action_fn: ActionFn
    scope_kind: Optional[ScopeKind] = None
    namespace_field: Optional[str] = None
    decision_fields: tuple[FieldSpec, ...] = ()
    possible_actions_override: Optional[frozenset[Action]] = None

    def __post_init__(self) -> None:
        resolved = self.possible_actions_override
        if resolved is None:
            if not isinstance(self.action_fn, _StaticActions):
                raise TypeError(
                    f"{self.id!r}: a field-conditional action_fn must pass "
                    "possible_actions_override explicitly"
                )
            resolved = self.action_fn.actions
        object.__setattr__(self, "possible_actions", resolved)

    @functools.cached_property
    def regex(self) -> re.Pattern[str]:
        return _compile(self.template)

    def matches(self, intent: ApiIntent) -> bool:
        """Method/path test only, kept independent of __call__ so callers
        that need this specific row (not just its recognized actions —
        policy.decide_scope, ApiGuard.parse/enrich/record/audit_fields) can
        find it via match_request without recomputing action_fn."""
        return intent.method.upper() in self.methods and bool(
            self.regex.fullmatch(intent.path.rstrip("/"))
        )

    def __call__(self, intent: ApiIntent) -> Optional[frozenset[Action]]:
        return self.action_fn(intent) if self.matches(intent) else None


def match_request(intent: ApiIntent) -> Optional[RestRecognizer]:
    """First CATALOG row matching intent, or None."""
    for row in CATALOG:
        if row.matches(intent):
            return row
    return None


_POST = _methods("POST")
_PUT = _methods("PUT")
_GET = _methods("GET")
_GET_HEAD = _methods("GET", "HEAD")

CATALOG: tuple[RestRecognizer, ...] = (
    # --- writes ---------------------------------------------------------
    RestRecognizer(
        id="mr.create",
        methods=_POST,
        template="/projects/{id}/merge_requests",
        action_fn=_static(git_actions.PROJECT_MR_CREATE),
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
        namespace_field="source_branch",
        decision_fields=(FieldSpec("source_branch", Location.BODY),),
    ),
    RestRecognizer(
        id="mr.note",
        methods=_POST,
        template="/projects/{id}/merge_requests/{iid}/notes",
        action_fn=_static(git_actions.PROJECT_MR_COMMENT),
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
    ),
    RestRecognizer(
        id="mr.discussion",
        methods=_POST,
        template="/projects/{id}/merge_requests/{iid}/discussions",
        action_fn=_static(git_actions.PROJECT_MR_COMMENT),
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
    ),
    RestRecognizer(
        id="mr.discussion_reply",
        methods=_POST,
        template="/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}/notes",
        action_fn=_static(git_actions.PROJECT_MR_COMMENT),
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
    ),
    RestRecognizer(
        id="mr.update",
        methods=_PUT,
        template="/projects/{id}/merge_requests/{iid}",
        action_fn=_mr_state_event,
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
        decision_fields=(FieldSpec("state_event", Location.BODY),),
        possible_actions_override=frozenset(_MR_STATE_ACTIONS.values()),
    ),
    RestRecognizer(
        id="mr.merge",
        methods=_PUT,
        template="/projects/{id}/merge_requests/{iid}/merge",
        action_fn=_static(git_actions.PROJECT_MR_MERGE),
        # QUOTA_BY_KIND, not BRANCH_NAMESPACE: the criticality gate always
        # denies this row before decide_scope is ever reached, so an iid ->
        # MR upstream lookup here would only be a wasted (unmocked-in-tests,
        # credential-backed) call for a request that can never be allowed.
        scope_kind=ScopeKind.QUOTA_BY_KIND,
    ),
    RestRecognizer(
        id="pipeline.trigger",
        methods=_POST,
        template="/projects/{id}/pipeline",
        action_fn=_static(git_actions.PROJECT_CI_TRIGGER),
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
        namespace_field="ref",
        decision_fields=(FieldSpec("ref", Location.BODY),),
    ),
    RestRecognizer(
        id="mr.pipeline.trigger",
        methods=_POST,
        template="/projects/{id}/merge_requests/{iid}/pipelines",
        action_fn=_static(git_actions.PROJECT_CI_TRIGGER),
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
    ),
    RestRecognizer(
        id="pipeline.retry",
        methods=_POST,
        template="/projects/{id}/pipelines/{pipeline_id}/retry",
        action_fn=_static(git_actions.PROJECT_CI_TRIGGER),
        scope_kind=ScopeKind.QUOTA_BY_KIND,
    ),
    RestRecognizer(
        id="pipeline.cancel",
        methods=_POST,
        template="/projects/{id}/pipelines/{pipeline_id}/cancel",
        action_fn=_static(git_actions.PROJECT_CI_TRIGGER),
        scope_kind=ScopeKind.QUOTA_BY_KIND,
    ),
    RestRecognizer(
        id="job.retry",
        methods=_POST,
        template="/projects/{id}/jobs/{job_id}/retry",
        action_fn=_static(git_actions.PROJECT_CI_TRIGGER),
        scope_kind=ScopeKind.QUOTA_BY_KIND,
    ),
    RestRecognizer(
        id="job.cancel",
        methods=_POST,
        template="/projects/{id}/jobs/{job_id}/cancel",
        action_fn=_static(git_actions.PROJECT_CI_TRIGGER),
        scope_kind=ScopeKind.QUOTA_BY_KIND,
    ),
    RestRecognizer(
        id="job.play",
        methods=_POST,
        template="/projects/{id}/jobs/{job_id}/play",
        action_fn=_static(git_actions.PROJECT_CI_TRIGGER),
        scope_kind=ScopeKind.QUOTA_BY_KIND,
    ),
    RestRecognizer(
        id="branch.create",
        methods=_POST,
        template="/projects/{id}/repository/branches",
        action_fn=_static(git_actions.REPO_BRANCH_CREATE),
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
        namespace_field="branch",
        decision_fields=(FieldSpec("branch", Location.BODY),),
    ),
    RestRecognizer(
        id="issue.create",
        methods=_POST,
        template="/projects/{id}/issues",
        action_fn=_static(git_actions.PROJECT_ISSUE_CREATE),
        scope_kind=ScopeKind.QUOTA_BY_KIND,
    ),
    RestRecognizer(
        id="issue.update",
        methods=_PUT,
        template="/projects/{id}/issues/{iid}",
        action_fn=_issue_state_event,
        scope_kind=ScopeKind.QUOTA_BY_KIND,
        decision_fields=(FieldSpec("state_event", Location.BODY),),
        possible_actions_override=frozenset(_ISSUE_STATE_ACTIONS.values()),
    ),
    RestRecognizer(
        id="issue.note",
        methods=_POST,
        template="/projects/{id}/issues/{iid}/notes",
        action_fn=_static(git_actions.PROJECT_ISSUE_COMMENT),
        scope_kind=ScopeKind.QUOTA_BY_KIND,
    ),
    # --- reads, project-bound (most specific first) ----------------------
    RestRecognizer(
        id="read.repository",
        methods=_GET_HEAD,
        template="/projects/{id}/repository/{rest}",
        action_fn=_static(git_actions.REPO_READ),
    ),
    RestRecognizer(
        id="read.artifacts",
        methods=_GET,
        template="/projects/{id}/jobs/{job_id}/artifacts{/rest}",
        action_fn=_static(git_actions.REPO_READ),
    ),
    RestRecognizer(
        id="read.snippets",
        methods=_GET,
        template="/projects/{id}/snippets{/rest}",
        action_fn=_static(git_actions.REPO_READ),
    ),
    # --- reads, projectless ------------------------------------------------
    RestRecognizer(
        id="read.search",
        methods=_GET,
        template="/search",
        action_fn=_search_scope,
        decision_fields=(FieldSpec("scope", Location.QUERY),),
        possible_actions_override=frozenset(_SEARCH_SCOPE_ACTIONS.values()),
    ),
    RestRecognizer(
        id="read.group_search",
        methods=_GET,
        template="/groups/{id}/search",
        action_fn=_search_scope,
        decision_fields=(FieldSpec("scope", Location.QUERY),),
        possible_actions_override=frozenset(_SEARCH_SCOPE_ACTIONS.values()),
    ),
    RestRecognizer(
        id="read.projects",
        methods=_GET,
        template="/projects",
        action_fn=_static(git_actions.INSTANCE_PROJECTS_READ),
    ),
    RestRecognizer(
        id="read.groups",
        methods=_GET,
        template="/groups{/rest}",
        action_fn=_static(git_actions.INSTANCE_PROJECTS_READ),
    ),
    RestRecognizer(
        id="read.merge_requests",
        methods=_GET,
        template="/merge_requests",
        action_fn=_static(git_actions.INSTANCE_PROJECTS_READ),
    ),
    RestRecognizer(
        id="read.issues",
        methods=_GET,
        template="/issues",
        action_fn=_static(git_actions.INSTANCE_PROJECTS_READ),
    ),
    RestRecognizer(
        id="read.users",
        methods=_GET,
        template="/users{/rest}",
        action_fn=_static(git_actions.INSTANCE_USERS_READ),
    ),
    RestRecognizer(
        id="read.user",
        methods=_GET,
        template="/user{/rest}",
        action_fn=_static(git_actions.INSTANCE_USERS_READ),
    ),
    RestRecognizer(
        id="read.events",
        methods=_GET,
        template="/events",
        action_fn=_static(git_actions.INSTANCE_USERS_READ),
    ),
    RestRecognizer(
        id="read.version",
        methods=_GET,
        template="/version",
        action_fn=_static(git_actions.INSTANCE_META_READ),
    ),
    RestRecognizer(
        id="read.metadata",
        methods=_GET,
        template="/metadata",
        action_fn=_static(git_actions.INSTANCE_META_READ),
    ),
    RestRecognizer(
        id="read.broadcast_messages",
        methods=_GET,
        template="/broadcast_messages",
        action_fn=_static(git_actions.INSTANCE_META_READ),
    ),
    # --- catch-all: every other project-bound read (last row on purpose) --
    RestRecognizer(
        id="read.project",
        methods=_GET_HEAD,
        template="/projects/{id}{/rest}",
        action_fn=_static(git_actions.PROJECT_READ),
    ),
)
