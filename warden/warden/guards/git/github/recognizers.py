"""The GitHub REST recognizer catalog: method + path template -> action set.

Matched first-match-wins, most specific row first; read.project is the
catch-all last row. An unrecognised field value, an undecidable body, a
disallowed body key, or a query string on a write yields an empty action
set, which is always a deny."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Optional

from ....core.actions import Action
from ....core.path_template import compile_template, has_dot_segment
from ....core.recognizer import Recognizer
from .. import actions as git_actions
from .intent import GithubIntent, IssueKind


class Scope(str, Enum):
    """The write-scope check policy.decide_scope performs for a row.

    BRANCH_FIELD: a branch named in the request body must lie in the
    namespace. PULL/RUN/JOB: the addressed pull request or CI run must be
    the agent's, resolved upstream. ISSUE_KIND: PULL when the issue
    number names a pull request, else QUOTA. QUOTA: project and quota only."""

    BRANCH_FIELD = "branch-field"
    PULL = "pull"
    RUN = "run"
    JOB = "job"
    ISSUE_KIND = "issue-kind"
    QUOTA = "quota"


ActionFn = Callable[[GithubIntent], "frozenset[Action]"]


def _one(action: Optional[Action]) -> frozenset[Action]:
    return frozenset({action}) if action is not None else frozenset()


def _static(action: Action) -> ActionFn:
    def fn(intent: GithubIntent) -> frozenset[Action]:
        return frozenset({action})

    return fn


# state absent edits; "open" reopens, which counts against the open-MR ceiling.
_PULL_STATE: Mapping[object, Action] = {
    None: git_actions.PROJECT_MR_EDIT,
    "closed": git_actions.PROJECT_MR_CLOSE,
    "open": git_actions.PROJECT_MR_CLOSE,
}
_ISSUE_STATE: Mapping[object, Action] = {
    None: git_actions.PROJECT_ISSUE_EDIT,
    "closed": git_actions.PROJECT_ISSUE_CLOSE,
    "open": git_actions.PROJECT_ISSUE_CLOSE,
}


def _pull_state(intent: GithubIntent) -> frozenset[Action]:
    return _one(_PULL_STATE.get(intent.fields.get("state")))


def _issue_update(intent: GithubIntent) -> frozenset[Action]:
    table = {IssueKind.PULL: _PULL_STATE, IssueKind.ISSUE: _ISSUE_STATE}.get(intent.issue_kind)
    return _one(table.get(intent.fields.get("state"))) if table is not None else frozenset()


def _issue_comment(intent: GithubIntent) -> frozenset[Action]:
    return _one(
        {
            IssueKind.PULL: git_actions.PROJECT_MR_COMMENT,
            IssueKind.ISSUE: git_actions.PROJECT_ISSUE_COMMENT,
        }.get(intent.issue_kind)
    )


def _review_create(intent: GithubIntent) -> frozenset[Action]:
    # No event leaves the review pending; only COMMENT may submit it.
    event = intent.fields.get("event")
    return _one(git_actions.PROJECT_MR_COMMENT if event in (None, "COMMENT") else None)


def _review_submit(intent: GithubIntent) -> frozenset[Action]:
    event = intent.fields.get("event")
    return _one(git_actions.PROJECT_MR_COMMENT if event == "COMMENT" else None)


def _ref_create(intent: GithubIntent) -> frozenset[Action]:
    ref = intent.fields.get("ref", "")
    if ref.startswith("refs/heads/"):
        return frozenset({git_actions.REPO_BRANCH_CREATE})
    if ref.startswith("refs/tags/"):
        return frozenset({git_actions.REPO_TAG_CREATE})
    return frozenset()


_REF_DELETE_RE = re.compile(r"/git/refs/(heads|tags)/.+$")


def _ref_delete(intent: GithubIntent) -> frozenset[Action]:
    m = _REF_DELETE_RE.search(intent.path)
    if m is None:
        return frozenset()
    if m.group(1) == "heads":
        return frozenset({git_actions.REPO_BRANCH_DELETE})
    return frozenset({git_actions.REPO_TAG_DELETE})


_SEARCH_RE = re.compile(r"^/search/([^/]+)$")
_SEARCH_SCOPE: Mapping[str, Action] = {
    "repositories": git_actions.INSTANCE_PROJECTS_READ,
    "issues": git_actions.INSTANCE_PROJECTS_READ,
    "users": git_actions.INSTANCE_USERS_READ,
}


def _search(intent: GithubIntent) -> frozenset[Action]:
    m = _SEARCH_RE.match(intent.path)
    return _one(_SEARCH_SCOPE.get(m.group(1)) if m else None)


def pull_head_branch(intent: GithubIntent) -> str:
    """The head branch of a pull-request create. head may be owner:branch;
    any owner other than the base repository's own yields "" (fork head)."""
    head = intent.fields.get("head", "")
    owner, sep, branch = head.partition(":")
    if not sep:
        return head
    return branch if owner.lower() == intent.repo_owner.lower() else ""


def heads_ref_branch(intent: GithubIntent) -> str:
    """ref as a branch name: bare or refs/heads/-qualified; any other
    qualified ref (a tag, a pull ref) yields ""."""
    ref = intent.fields.get("ref", "")
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/") :]
    return "" if ref.startswith("refs/") else ref


def _methods(*methods: str) -> frozenset[str]:
    return frozenset(methods)


@dataclass(frozen=True, kw_only=True)
class GithubRecognizer(Recognizer[GithubIntent]):
    """One catalog row: method(s) + path template -> action set.

    allowed_body_keys, when set, is the complete allowlist of body keys
    for the row; namespace_fn yields the branch a BRANCH_FIELD row checks."""

    id: str
    methods: frozenset[str]
    template: str
    action_fn: ActionFn
    possible_actions: frozenset[Action]
    scope: Optional[Scope] = None
    namespace_fn: Optional[Callable[[GithubIntent], str]] = None
    decision_fields: tuple[str, ...] = ()
    allowed_body_keys: Optional[frozenset[str]] = None

    @functools.cached_property
    def regex(self) -> re.Pattern[str]:
        return compile_template(self.template)

    def matches(self, intent: GithubIntent) -> bool:
        if has_dot_segment(intent.path):
            return False
        return intent.method.upper() in self.methods and bool(
            self.regex.fullmatch(intent.path.rstrip("/"))
        )

    def __call__(self, intent: GithubIntent) -> Optional[frozenset[Action]]:
        if not self.matches(intent):
            return None
        if intent.needs_write and not _write_wire_ok(self, intent):
            return frozenset()
        return self.action_fn(intent)

    def effective_scope(self, intent: GithubIntent) -> Optional[Scope]:
        if self.scope is Scope.ISSUE_KIND:
            return Scope.PULL if intent.issue_kind is IssueKind.PULL else Scope.QUOTA
        return self.scope


def _write_wire_ok(row: GithubRecognizer, intent: GithubIntent) -> bool:
    """A write is decidable only with no query string (the upstream merges
    query parameters into the body), a body_ok body, and only allowed keys."""
    if intent.raw_query or not intent.body_ok:
        return False
    allowed = row.allowed_body_keys
    return allowed is None or intent.body_keys <= allowed


def match_request(intent: GithubIntent) -> Optional[GithubRecognizer]:
    """First CATALOG row matching intent, or None."""
    for row in CATALOG:
        if row.matches(intent):
            return row
    return None


def _row(
    id: str, methods: frozenset[str], template: str, action: Action, **kw: object
) -> GithubRecognizer:
    """A row whose action set is static."""
    return GithubRecognizer(
        id=id,
        methods=methods,
        template=template,
        action_fn=_static(action),
        possible_actions=frozenset({action}),
        **kw,  # type: ignore[arg-type]
    )


_POST = _methods("POST")
_PATCH = _methods("PATCH")
_PUT = _methods("PUT")
_DELETE = _methods("DELETE")
_GET = _methods("GET")
_GET_HEAD = _methods("GET", "HEAD")

_REPO = "/repos/{owner}/{repo}"
_A = git_actions

CATALOG: tuple[GithubRecognizer, ...] = (
    # --- writes: pull requests -------------------------------------------
    _row(
        "pull.create",
        _POST,
        f"{_REPO}/pulls",
        _A.PROJECT_MR_CREATE,
        scope=Scope.BRANCH_FIELD,
        namespace_fn=pull_head_branch,
        decision_fields=("head",),
        allowed_body_keys=frozenset(
            {"title", "head", "base", "body", "draft", "maintainer_can_modify"}
        ),
    ),
    GithubRecognizer(
        id="pull.update",
        methods=_PATCH,
        template=f"{_REPO}/pulls/{{number:int}}",
        action_fn=_pull_state,
        possible_actions=frozenset(_PULL_STATE.values()),
        scope=Scope.PULL,
        decision_fields=("state",),
        allowed_body_keys=frozenset({"title", "body", "state", "base", "maintainer_can_modify"}),
    ),
    _row(
        "pull.merge",
        _PUT,
        f"{_REPO}/pulls/{{number:int}}/merge",
        _A.PROJECT_MR_MERGE,
        # QUOTA, not PULL: the criticality gate always denies this row first.
        scope=Scope.QUOTA,
    ),
    _row(
        "pull.review_comment",
        _POST,
        f"{_REPO}/pulls/{{number:int}}/comments",
        _A.PROJECT_MR_COMMENT,
        scope=Scope.PULL,
    ),
    _row(
        "pull.review_comment_reply",
        _POST,
        f"{_REPO}/pulls/{{number:int}}/comments/{{comment_id:int}}/replies",
        _A.PROJECT_MR_COMMENT,
        scope=Scope.PULL,
    ),
    GithubRecognizer(
        id="pull.review",
        methods=_POST,
        template=f"{_REPO}/pulls/{{number:int}}/reviews",
        action_fn=_review_create,
        possible_actions=frozenset({_A.PROJECT_MR_COMMENT}),
        scope=Scope.PULL,
        decision_fields=("event",),
        allowed_body_keys=frozenset({"commit_id", "body", "event", "comments"}),
    ),
    GithubRecognizer(
        id="pull.review_submit",
        methods=_POST,
        template=f"{_REPO}/pulls/{{number:int}}/reviews/{{review_id:int}}/events",
        action_fn=_review_submit,
        possible_actions=frozenset({_A.PROJECT_MR_COMMENT}),
        scope=Scope.PULL,
        decision_fields=("event",),
        allowed_body_keys=frozenset({"body", "event"}),
    ),
    # --- writes: issues, which may name a pull request -------------------
    _row(
        "issue.create",
        _POST,
        f"{_REPO}/issues",
        _A.PROJECT_ISSUE_CREATE,
        scope=Scope.QUOTA,
    ),
    GithubRecognizer(
        id="issue.update",
        methods=_PATCH,
        template=f"{_REPO}/issues/{{number:int}}",
        action_fn=_issue_update,
        possible_actions=frozenset({*_PULL_STATE.values(), *_ISSUE_STATE.values()}),
        scope=Scope.ISSUE_KIND,
        decision_fields=("state",),
    ),
    GithubRecognizer(
        id="issue.comment",
        methods=_POST,
        template=f"{_REPO}/issues/{{number:int}}/comments",
        action_fn=_issue_comment,
        possible_actions=frozenset({_A.PROJECT_MR_COMMENT, _A.PROJECT_ISSUE_COMMENT}),
        scope=Scope.ISSUE_KIND,
    ),
    # --- writes: CI (GitHub Actions) -------------------------------------
    _row(
        "workflow.dispatch",
        _POST,
        f"{_REPO}/actions/workflows/{{workflow_id}}/dispatches",
        _A.PROJECT_CI_TRIGGER,
        scope=Scope.BRANCH_FIELD,
        namespace_fn=heads_ref_branch,
        decision_fields=("ref",),
        allowed_body_keys=frozenset({"ref", "inputs"}),
    ),
    _row(
        "run.rerun",
        _POST,
        f"{_REPO}/actions/runs/{{run_id:int}}/rerun",
        _A.PROJECT_CI_TRIGGER,
        scope=Scope.RUN,
    ),
    _row(
        "run.rerun_failed",
        _POST,
        f"{_REPO}/actions/runs/{{run_id:int}}/rerun-failed-jobs",
        _A.PROJECT_CI_TRIGGER,
        scope=Scope.RUN,
    ),
    _row(
        "run.cancel",
        _POST,
        f"{_REPO}/actions/runs/{{run_id:int}}/cancel",
        _A.PROJECT_CI_TRIGGER,
        scope=Scope.RUN,
    ),
    _row(
        "job.rerun",
        _POST,
        f"{_REPO}/actions/jobs/{{job_id:int}}/rerun",
        _A.PROJECT_CI_TRIGGER,
        scope=Scope.JOB,
    ),
    # --- writes: refs ----------------------------------------------------
    GithubRecognizer(
        id="ref.create",
        methods=_POST,
        template=f"{_REPO}/git/refs",
        action_fn=_ref_create,
        possible_actions=frozenset({_A.REPO_BRANCH_CREATE, _A.REPO_TAG_CREATE}),
        scope=Scope.BRANCH_FIELD,
        namespace_fn=heads_ref_branch,
        decision_fields=("ref",),
        allowed_body_keys=frozenset({"ref", "sha"}),
    ),
    GithubRecognizer(
        id="ref.delete",
        methods=_DELETE,
        template=f"{_REPO}/git/refs/{{rest}}",
        action_fn=_ref_delete,
        possible_actions=frozenset({_A.REPO_BRANCH_DELETE, _A.REPO_TAG_DELETE}),
        scope=Scope.QUOTA,
    ),
    # --- reads, repository-bound (most specific first) -------------------
    *(
        _row(f"read.{name}", _GET_HEAD, f"{_REPO}/{name}{{/rest}}", _A.REPO_READ)
        for name in (
            "contents",
            "git",
            "commits",
            "branches",
            "tags",
            "compare",
            "tarball",
            "zipball",
            "readme",
        )
    ),
    _row("read.artifacts", _GET, f"{_REPO}/actions/artifacts{{/rest}}", _A.REPO_READ),
    # --- reads, repositoryless -------------------------------------------
    GithubRecognizer(
        id="read.search",
        methods=_GET,
        template="/search/{scope}",
        action_fn=_search,
        possible_actions=frozenset(_SEARCH_SCOPE.values()),
    ),
    _row("read.repositories", _GET, "/repositories", _A.INSTANCE_PROJECTS_READ),
    _row("read.orgs", _GET, "/orgs{/rest}", _A.INSTANCE_PROJECTS_READ),
    _row("read.organizations", _GET, "/organizations", _A.INSTANCE_PROJECTS_READ),
    _row("read.issues", _GET, "/issues", _A.INSTANCE_PROJECTS_READ),
    _row("read.users", _GET, "/users{/rest}", _A.INSTANCE_USERS_READ),
    _row("read.user", _GET, "/user{/rest}", _A.INSTANCE_USERS_READ),
    _row("read.events", _GET, "/events", _A.INSTANCE_USERS_READ),
    _row("read.meta", _GET, "/meta", _A.INSTANCE_META_READ),
    _row("read.rate_limit", _GET, "/rate_limit", _A.INSTANCE_META_READ),
    _row("read.versions", _GET, "/versions", _A.INSTANCE_META_READ),
    # --- catch-all: every other repository-bound read (last on purpose) --
    _row("read.project", _GET_HEAD, f"{_REPO}{{/rest}}", _A.PROJECT_READ),
)
