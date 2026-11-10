"""Pure catalog, parsing and policy behaviour of the GitHub REST guard."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from warden.core.model import StateView, TokenKind
from warden.core.path_template import compile_template, has_dot_segment
from warden.guards.git import actions as A
from warden.guards.git.github import actions as github_actions
from warden.guards.git.github import policy
from warden.guards.git.github.intent import IssueKind
from warden.guards.git.github.lookup import pull_is_agents, repo_path, run_is_agents
from warden.guards.git.github.parsing import (
    decision_fields,
    parse_json_object,
    project_from_path,
    raw_query,
    raw_rest_path,
)
from warden.guards.git.github.recognizers import (
    CATALOG,
    Scope,
    heads_ref_branch,
    match_request,
    pull_head_branch,
)

from .github_helpers import intent as mk
from .github_helpers import make_cfg, pull_json, run_json

R = "/repos/octo/repo"


def recognized(i):
    return policy.recognize(i)[1]


def ids(actions):
    return {a.id for a in actions}


# --- vocabulary ---------------------------------------------------------------
def test_supported_is_exactly_the_union_of_catalog_possible_actions():
    union = frozenset().union(*(row.possible_actions for row in CATALOG))
    assert github_actions.SUPPORTED == union


def test_catalog_row_ids_are_unique_and_catch_all_is_last():
    row_ids = [row.id for row in CATALOG]
    assert len(row_ids) == len(set(row_ids))
    assert row_ids[-1] == "read.project"


# --- path templates ------------------------------------------------------------
@pytest.mark.parametrize(
    "template,path,ok",
    [
        ("/a/{n:int}", "/a/12", True),
        ("/a/{n:int}", "/a/x1", False),
        ("/a/{n}", "/a/x", True),
        ("/a/{n}", "/a/x/y", False),
        ("/a/{rest}", "/a/x/y", True),
        ("/a/{rest}", "/a/", False),
        ("/a{/rest}", "/a", True),
        ("/a{/rest}", "/a/b/c", True),
        ("/a{/rest}", "/ab", False),
        ("/a.b", "/axb", False),
    ],
)
def test_compile_template(template, path, ok):
    assert bool(compile_template(template).fullmatch(path)) is ok


@pytest.mark.parametrize(
    "path,dot",
    [
        ("/a/../b", True),
        ("/a/./b", True),
        ("/a/%2e%2e/b", True),
        ("/a/%2E/b", True),
        ("/a/x%2F..%2Fy", True),
        ("/a/..b/c", False),
        ("/a/b.../c", False),
    ],
)
def test_has_dot_segment(path, dot):
    assert has_dot_segment(path) is dot


@pytest.mark.parametrize(
    "path",
    [
        f"{R}/pulls/../pulls",
        f"{R}/contents/%2e%2e/x",
        f"{R}/./pulls",
        f"{R}/git/refs/heads/%2E%2E/main",
    ],
)
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_dot_segments_never_match_any_row(path, method):
    assert match_request(mk(method, path)) is None


# --- parsing ---------------------------------------------------------------
def _request(raw_path: bytes | None, path: str, query: bytes = b"") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "query_string": query, "headers": []}
    if raw_path is not None:
        scope["raw_path"] = raw_path
    return Request(scope)


def test_raw_rest_path_decodes_only_unreserved_escapes():
    req = _request(b"/api/v3/repos/o/r/%63ontents/a%2Fb?x=1", "/api/v3/repos/o/r/contents", b"x=1")
    assert raw_rest_path(req) == "/repos/o/r/contents/a%2Fb"
    assert raw_query(req) == "x=1"


def test_raw_rest_path_falls_back_to_decoded_path_and_root():
    assert raw_rest_path(_request(None, "/api/v3/meta")) == "/meta"
    assert raw_rest_path(_request(b"/api/v3", "/api/v3")) == "/"


def test_project_from_path_decodes_segments():
    assert project_from_path("/repos/o%2Dx/r/pulls") == "o-x/r"
    assert project_from_path("/user") == ""


def test_repo_path_reencodes_segments():
    assert repo_path("o x/r/y") == "repos/o%20x/r%2Fy"


@pytest.mark.parametrize(
    "body,expected",
    [
        (b"", {}),
        (b'{"a": "b"}', {"a": "b"}),
        (b"[1]", None),
        (b'"x"', None),
        (b"head=claude/x", None),
        (b"{bad", None),
        (b'{"a": 1, "a": 2}', None),
        (b'{"head": "claude/x", "he\\u0061d": "main"}', None),
        (b"\xff\xfe\x00", None),
    ],
)
def test_parse_json_object(body, expected):
    assert parse_json_object(body) == expected


def test_decision_fields_types():
    assert decision_fields(None, ("a",)) is None
    assert decision_fields({"a": "x", "b": 1}, ("a", "c")) == {"a": "x"}
    assert decision_fields({"a": None}, ("a",)) is None
    assert decision_fields({"a": ["x"]}, ("a",)) is None


# --- branch extraction ----------------------------------------------------------
@pytest.mark.parametrize(
    "head,branch",
    [
        ("claude/x", "claude/x"),
        ("octo:claude/x", "claude/x"),
        ("OcTo:claude/x", "claude/x"),
        ("evil:claude/x", ""),
        (":claude/x", ""),
        ("", ""),
    ],
)
def test_pull_head_branch(head, branch):
    assert pull_head_branch(mk("POST", f"{R}/pulls", fields={"head": head})) == branch


@pytest.mark.parametrize(
    "ref,branch",
    [
        ("claude/x", "claude/x"),
        ("refs/heads/claude/x", "claude/x"),
        ("refs/tags/claude/x", ""),
        ("refs/pull/1/head", ""),
        ("", ""),
    ],
)
def test_heads_ref_branch(ref, branch):
    assert heads_ref_branch(mk("POST", f"{R}/git/refs", fields={"ref": ref})) == branch


# --- recognition ------------------------------------------------------------------
@pytest.mark.parametrize(
    "state,action",
    [(None, A.PROJECT_MR_EDIT), ("closed", A.PROJECT_MR_CLOSE), ("open", A.PROJECT_MR_CLOSE)],
)
def test_pull_update_state(state, action):
    fields = {} if state is None else {"state": state}
    assert recognized(mk("PATCH", f"{R}/pulls/7", fields=fields)) == {action}


@pytest.mark.parametrize("state", ["merged", "OPEN", "Closed", ""])
def test_pull_update_unknown_state_is_empty(state):
    assert recognized(mk("PATCH", f"{R}/pulls/7", fields={"state": state})) == frozenset()


@pytest.mark.parametrize(
    "kind,state,action",
    [
        (IssueKind.PULL, None, A.PROJECT_MR_EDIT),
        (IssueKind.PULL, "closed", A.PROJECT_MR_CLOSE),
        (IssueKind.ISSUE, None, A.PROJECT_ISSUE_EDIT),
        (IssueKind.ISSUE, "open", A.PROJECT_ISSUE_CLOSE),
    ],
)
def test_issue_update_by_kind(kind, state, action):
    fields = {} if state is None else {"state": state}
    i = mk("PATCH", f"{R}/issues/7", fields=fields, issue_kind=kind)
    assert recognized(i) == {action}


def test_issue_rows_with_unknown_kind_are_empty():
    assert recognized(mk("PATCH", f"{R}/issues/7")) == frozenset()
    assert recognized(mk("POST", f"{R}/issues/7/comments")) == frozenset()
    assert (
        recognized(mk("PATCH", f"{R}/issues/7", fields={"state": "x"}, issue_kind=IssueKind.ISSUE))
        == frozenset()
    )


@pytest.mark.parametrize(
    "kind,action",
    [(IssueKind.PULL, A.PROJECT_MR_COMMENT), (IssueKind.ISSUE, A.PROJECT_ISSUE_COMMENT)],
)
def test_issue_comment_by_kind(kind, action):
    assert recognized(mk("POST", f"{R}/issues/7/comments", issue_kind=kind)) == {action}


def test_effective_scope_of_issue_rows_follows_kind():
    row = match_request(mk("PATCH", f"{R}/issues/7"))
    assert row.scope is Scope.ISSUE_KIND
    assert (
        row.effective_scope(mk("PATCH", f"{R}/issues/7", issue_kind=IssueKind.PULL)) is Scope.PULL
    )
    assert (
        row.effective_scope(mk("PATCH", f"{R}/issues/7", issue_kind=IssueKind.ISSUE)) is Scope.QUOTA
    )
    assert row.effective_scope(mk("PATCH", f"{R}/issues/7")) is Scope.QUOTA


@pytest.mark.parametrize(
    "path,fields,expected",
    [
        (f"{R}/pulls/7/reviews", {}, {A.PROJECT_MR_COMMENT}),
        (f"{R}/pulls/7/reviews", {"event": "COMMENT"}, {A.PROJECT_MR_COMMENT}),
        (f"{R}/pulls/7/reviews", {"event": "APPROVE"}, set()),
        (f"{R}/pulls/7/reviews", {"event": "REQUEST_CHANGES"}, set()),
        (f"{R}/pulls/7/reviews", {"event": "comment"}, set()),
        (f"{R}/pulls/7/reviews/5/events", {"event": "COMMENT"}, {A.PROJECT_MR_COMMENT}),
        (f"{R}/pulls/7/reviews/5/events", {}, set()),
        (f"{R}/pulls/7/reviews/5/events", {"event": "APPROVE"}, set()),
    ],
)
def test_review_events(path, fields, expected):
    assert recognized(mk("POST", path, fields=fields)) == expected


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("refs/heads/claude/x", {A.REPO_BRANCH_CREATE}),
        ("refs/tags/v1", {A.REPO_TAG_CREATE}),
        ("refs/pull/1/head", set()),
        ("claude/x", set()),
    ],
)
def test_ref_create(ref, expected):
    assert recognized(mk("POST", f"{R}/git/refs", fields={"ref": ref})) == expected


@pytest.mark.parametrize(
    "rest,expected",
    [
        ("heads/claude/x", {A.REPO_BRANCH_DELETE}),
        ("tags/v1", {A.REPO_TAG_DELETE}),
        ("heads", set()),
        ("pull/1/head", set()),
        ("heads%2Fclaude%2Fx", set()),
    ],
)
def test_ref_delete(rest, expected):
    assert recognized(mk("DELETE", f"{R}/git/refs/{rest}")) == expected


@pytest.mark.parametrize(
    "scope,expected",
    [
        ("repositories", {A.INSTANCE_PROJECTS_READ}),
        ("issues", {A.INSTANCE_PROJECTS_READ}),
        ("users", {A.INSTANCE_USERS_READ}),
        ("code", set()),
        ("commits", set()),
        ("labels", set()),
        ("topics", set()),
    ],
)
def test_search_scopes(scope, expected):
    assert recognized(mk("GET", f"/search/{scope}")) == expected


def test_write_with_query_string_is_empty():
    i = mk("POST", f"{R}/pulls", fields={"head": "claude/x"}, raw_query="head=main")
    assert recognized(i) == frozenset()


def test_write_with_undecidable_body_is_empty():
    assert recognized(mk("POST", f"{R}/pulls/7/comments", body_ok=False)) == frozenset()


@pytest.mark.parametrize(
    "path,keys",
    [
        (f"{R}/pulls", ("head", "issue")),
        (f"{R}/pulls", ("head", "head_repo")),
        (f"{R}/pulls/7", ("state", "head")),
        (f"{R}/pulls/7/reviews", ("event", "x")),
        (f"{R}/actions/workflows/ci.yml/dispatches", ("ref", "extra")),
        (f"{R}/git/refs", ("ref", "sha", "force")),
    ],
)
def test_disallowed_body_keys_are_empty(path, keys):
    method = "PATCH" if path.endswith("/7") else "POST"
    assert (
        recognized(mk(method, path, body_keys=keys, fields={"ref": "refs/heads/claude/x"}))
        == frozenset()
    )


def test_trailing_slash_still_matches():
    assert recognized(mk("PUT", f"{R}/pulls/7/merge/")) == {A.PROJECT_MR_MERGE}


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_head_and_get_reads(method):
    assert recognized(mk(method, f"{R}/contents/a")) == {A.REPO_READ}


def test_graphql_intent_never_needs_write():
    for path in ("/graphql", "/graphql/x"):
        i = mk("POST", path)
        assert i.is_graphql and not i.needs_write
    assert not mk("POST", "/graphqlx").is_graphql


# --- ownership rules --------------------------------------------------------------
@pytest.mark.parametrize(
    "pull,ok",
    [
        (pull_json(), True),
        (pull_json(ref="main"), False),
        (pull_json(head_id=2), False),
        (pull_json(head_id=None), False),
        (
            {"head": {"ref": "claude/x", "repo": {"id": True}}, "base": {"repo": {"id": True}}},
            False,
        ),
        ({"head": {"ref": 1, "repo": {"id": 1}}, "base": {"repo": {"id": 1}}}, False),
        ({"head": None, "base": None}, False),
        ([], False),
    ],
)
def test_pull_is_agents(pull, ok):
    assert pull_is_agents(make_cfg(), "github.com", pull) is ok


@pytest.mark.parametrize(
    "run,ok",
    [
        (run_json(), True),
        (run_json(branch="main"), False),
        (run_json(head_id=2), False),
        ({"head_branch": "claude/x", "head_repository": None, "repository": {"id": 1}}, False),
        ({"head_branch": None, "head_repository": {"id": 1}, "repository": {"id": 1}}, False),
        ("x", False),
    ],
)
def test_run_is_agents(run, ok):
    assert run_is_agents(make_cfg(), "github.com", run) is ok


# --- policy.decide (pure) -----------------------------------------------------------
CFG = make_cfg()
OPEN = StateView()


def _decide(i, state=OPEN):
    return policy.decide(i, state, CFG)


def test_decide_read_pass_through_even_when_locked():
    d = _decide(mk("GET", f"{R}/pulls"), StateView(locked=True))
    assert d.allow and d.token is TokenKind.READ


def test_decide_unrecognized_read_and_write():
    assert "read endpoint not in allowlist" in _decide(mk("GET", "/gists")).reason
    assert "write endpoint not in allowlist" in _decide(mk("POST", "/gists")).reason


def test_decide_graphql():
    assert "GraphQL" in _decide(mk("POST", "/graphql")).reason


@pytest.mark.parametrize(
    "scope_ok,reason",
    [(None, "could not be verified"), (False, "is not on an agent branch of this repository")],
)
@pytest.mark.parametrize(
    "path,what",
    [
        (f"{R}/pulls/7/comments", "pull request"),
        (f"{R}/actions/runs/9/rerun", "workflow run"),
        (f"{R}/actions/jobs/11/rerun", "workflow job"),
    ],
)
def test_decide_unverified_scopes(path, what, scope_ok, reason):
    d = _decide(mk("POST", path, scope_ok=scope_ok))
    assert not d.allow and d.reason == f"{what} {reason}"


def test_decide_scope_ok_write_uses_write_token():
    d = _decide(mk("POST", f"{R}/pulls/7/comments", scope_ok=True))
    assert d.allow and d.token is TokenKind.WRITE


def test_decide_branch_field_denial_names_prefixes():
    d = _decide(mk("POST", f"{R}/pulls", fields={"head": "main"}))
    assert d.reason == "branch 'main' outside allowed prefixes ('claude/',)"


@pytest.mark.parametrize(
    "state,reason",
    [
        (StateView(locked=True), "state locked"),
        (StateView(writes_last_hour=60), "rate limit reached (60/h)"),
        (StateView(open_mrs=5), "max open MRs reached (5)"),
    ],
)
def test_decide_quota_on_create(state, reason):
    d = _decide(mk("POST", f"{R}/pulls", fields={"head": "claude/x"}), state)
    assert not d.allow and reason in d.reason


def test_decide_open_mr_ceiling_applies_to_reopen_not_close_or_edit():
    full = StateView(open_mrs=5)
    reopen = mk("PATCH", f"{R}/pulls/7", fields={"state": "open"}, scope_ok=True)
    close = mk("PATCH", f"{R}/pulls/7", fields={"state": "closed"}, scope_ok=True)
    edit = mk("PATCH", f"{R}/pulls/7", scope_ok=True)
    assert "max open MRs" in _decide(reopen, full).reason
    assert _decide(close, full).allow and _decide(edit, full).allow


def test_decide_issue_reopen_does_not_count_against_mr_ceiling():
    i = mk("PATCH", f"{R}/issues/7", fields={"state": "open"}, issue_kind=IssueKind.ISSUE)
    assert _decide(i, StateView(open_mrs=5)).allow
