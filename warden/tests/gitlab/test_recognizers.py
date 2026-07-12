"""Table-driven tests for the GitLab REST recognizer catalog.

Every row is exercised at least once; field-conditional rows (mr.update,
issue.update, the two search rows) get their own matrix. Content line,
GraphQL, and the merge criticality-deny are each pinned down explicitly."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from warden.core.config import Config, GitEndpoint, HostCredentials
from warden.core.model import StateView, TokenKind
from warden.guards.git import actions as git_actions
from warden.guards.git.gitlab.intent import ApiIntent
from warden.guards.git.gitlab.policy import full_decide
from warden.guards.git.gitlab.recognizers import CATALOG, match_request

HOST = "gitlab.example"


def _intent(method: str, path: str, **fields: object) -> ApiIntent:
    project = "group/proj" if "/projects/" in path else ""
    return ApiIntent(_project=project, _method=method, path=path, fields=dict(fields), _host=HOST)


def _cfg() -> Config:
    return Config(
        git_endpoints=(GitEndpoint(host=HOST, type="gitlab", allowed_projects=("group/proj",)),),
        git_credentials={HOST: HostCredentials(read_token="r", write_token="w")},
    )


# --- every catalog row: match + recognized action set -----------------------


@dataclass(frozen=True)
class Case:
    expected_id: str
    method: str
    path: str
    fields: dict[str, object]
    expected_actions: tuple[str, ...]


CATALOG_CASES: list[Case] = [
    Case(
        "mr.create",
        "POST",
        "/projects/1/merge_requests",
        {"source_branch": "claude/x"},
        ("project.mr.create",),
    ),
    Case("mr.note", "POST", "/projects/1/merge_requests/7/notes", {}, ("project.mr.comment",)),
    Case(
        "mr.discussion",
        "POST",
        "/projects/1/merge_requests/7/discussions",
        {},
        ("project.mr.comment",),
    ),
    Case(
        "mr.discussion_reply",
        "POST",
        "/projects/1/merge_requests/7/discussions/99/notes",
        {},
        ("project.mr.comment",),
    ),
    Case("mr.update", "PUT", "/projects/1/merge_requests/7", {}, ("project.mr.edit",)),
    Case("mr.merge", "PUT", "/projects/1/merge_requests/7/merge", {}, ("project.mr.merge",)),
    Case(
        "pipeline.trigger",
        "POST",
        "/projects/1/pipeline",
        {"ref": "claude/x"},
        ("project.ci.trigger",),
    ),
    Case(
        "mr.pipeline.trigger",
        "POST",
        "/projects/1/merge_requests/7/pipelines",
        {},
        ("project.ci.trigger",),
    ),
    Case("pipeline.retry", "POST", "/projects/1/pipelines/9/retry", {}, ("project.ci.trigger",)),
    Case("pipeline.cancel", "POST", "/projects/1/pipelines/9/cancel", {}, ("project.ci.trigger",)),
    Case("job.retry", "POST", "/projects/1/jobs/9/retry", {}, ("project.ci.trigger",)),
    Case("job.cancel", "POST", "/projects/1/jobs/9/cancel", {}, ("project.ci.trigger",)),
    Case("job.play", "POST", "/projects/1/jobs/9/play", {}, ("project.ci.trigger",)),
    Case(
        "branch.create",
        "POST",
        "/projects/1/repository/branches",
        {"branch": "claude/x"},
        ("repo.branch.create",),
    ),
    Case("issue.create", "POST", "/projects/1/issues", {}, ("project.issue.create",)),
    Case("issue.update", "PUT", "/projects/1/issues/7", {}, ("project.issue.edit",)),
    Case("issue.note", "POST", "/projects/1/issues/7/notes", {}, ("project.issue.comment",)),
    Case("read.repository", "GET", "/projects/1/repository/tree", {}, ("repo.read",)),
    Case("read.artifacts", "GET", "/projects/1/jobs/9/artifacts", {}, ("repo.read",)),
    Case("read.snippets", "GET", "/projects/1/snippets", {}, ("repo.read",)),
    Case("read.search", "GET", "/search", {"scope": "projects"}, ("instance.projects.read",)),
    Case(
        "read.group_search", "GET", "/groups/1/search", {"scope": "users"}, ("instance.users.read",)
    ),
    Case("read.projects", "GET", "/projects", {}, ("instance.projects.read",)),
    Case("read.groups", "GET", "/groups/1", {}, ("instance.projects.read",)),
    Case("read.merge_requests", "GET", "/merge_requests", {}, ("instance.projects.read",)),
    Case("read.issues", "GET", "/issues", {}, ("instance.projects.read",)),
    Case("read.users", "GET", "/users/7", {}, ("instance.users.read",)),
    Case("read.user", "GET", "/user", {}, ("instance.users.read",)),
    Case("read.events", "GET", "/events", {}, ("instance.users.read",)),
    Case("read.version", "GET", "/version", {}, ("instance.meta.read",)),
    Case("read.metadata", "GET", "/metadata", {}, ("instance.meta.read",)),
    Case("read.broadcast_messages", "GET", "/broadcast_messages", {}, ("instance.meta.read",)),
    Case("read.project", "GET", "/projects/1/merge_requests/7/diffs", {}, ("project.read",)),
]


@pytest.mark.parametrize("case", CATALOG_CASES, ids=lambda c: c.expected_id)
def test_catalog_row_matches_and_recognizes(case: Case):
    intent = _intent(case.method, case.path, **case.fields)
    match = match_request(intent)
    assert match is not None, f"no recognizer matched {case.method} {case.path}"
    assert match.id == case.expected_id
    recognized = match(intent)
    assert {a.id for a in recognized} == set(case.expected_actions)


def test_every_catalog_row_is_exercised_by_the_table():
    tested_ids = {case.expected_id for case in CATALOG_CASES}
    all_ids = {row.id for row in CATALOG}
    assert tested_ids == all_ids


# --- state_event matrix: MR ---------------------------------------------------


@pytest.mark.parametrize(
    "state_event,expected",
    [
        (None, {"project.mr.edit"}),
        ("close", {"project.mr.close"}),
        ("reopen", {"project.mr.close"}),
        ("merge", {"project.mr.merge"}),
        ("bogus", set()),
    ],
)
def test_mr_update_state_event_matrix(state_event, expected):
    fields = {} if state_event is None else {"state_event": state_event}
    intent = _intent("PUT", "/projects/1/merge_requests/7", **fields)
    match = match_request(intent)
    assert match is not None and match.id == "mr.update"
    assert {a.id for a in match(intent)} == expected


# --- state_event matrix: issues (no merge concept) ----------------------------


@pytest.mark.parametrize(
    "state_event,expected",
    [
        (None, {"project.issue.edit"}),
        ("close", {"project.issue.close"}),
        ("reopen", {"project.issue.close"}),
        ("merge", set()),  # issues have no merge alias — an unknown value here
        ("bogus", set()),
    ],
)
def test_issue_update_state_event_matrix(state_event, expected):
    fields = {} if state_event is None else {"state_event": state_event}
    intent = _intent("PUT", "/projects/1/issues/7", **fields)
    match = match_request(intent)
    assert match is not None and match.id == "issue.update"
    assert {a.id for a in match(intent)} == expected


# --- search scope matrix -------------------------------------------------------


@pytest.mark.parametrize(
    "scope,expected",
    [
        ("projects", {"instance.projects.read"}),
        ("merge_requests", {"instance.projects.read"}),
        ("issues", {"instance.projects.read"}),
        ("milestones", {"instance.projects.read"}),
        ("users", {"instance.users.read"}),
        ("blobs", set()),
        ("commits", set()),
        (None, set()),
    ],
)
def test_search_scope_matrix(scope, expected):
    fields = {} if scope is None else {"scope": scope}
    intent = _intent("GET", "/search", **fields)
    match = match_request(intent)
    assert match is not None and match.id == "read.search"
    assert {a.id for a in match(intent)} == expected


def test_group_search_unknown_scope_denied():
    intent = _intent("GET", "/groups/1/search", scope="wiki_blobs")
    match = match_request(intent)
    assert match is not None and match.id == "read.group_search"
    assert match(intent) == frozenset()


# --- the content line ----------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected_action",
    [
        ("/projects/1/repository/tree", "repo.read"),
        ("/projects/1/repository/files/README.md/raw", "repo.read"),
        ("/projects/1/jobs/9/artifacts", "repo.read"),
        ("/projects/1/jobs/9/artifacts/build/out.zip", "repo.read"),
        ("/projects/1/snippets", "repo.read"),
        ("/projects/1/snippets/5/raw", "repo.read"),
        ("/projects/1/merge_requests/7/diffs", "project.read"),
        ("/projects/1/merge_requests/7/changes", "project.read"),
        ("/projects/1/merge_requests/7/versions", "project.read"),
        ("/projects/1/jobs/9/trace", "project.read"),
    ],
)
def test_content_line(path, expected_action):
    intent = _intent("GET", path)
    match = match_request(intent)
    assert match is not None
    assert {a.id for a in match(intent)} == {expected_action}


@pytest.mark.parametrize(
    "path",
    [
        "/projects/group%2Fproj/repository/tree",
        "/projects/group%2Fproj/jobs/9/artifacts",
    ],
)
def test_repo_read_disabled_denies_file_and_artifact_reads_even_with_project_read(path):
    # project.read enabled, repo.read is not — the content line still bites.
    d = full_decide(
        _intent("GET", path),
        StateView(),
        _cfg(),
        frozenset({"project.read", "instance.projects.read"}),
    )
    assert not d.allow


def test_repo_read_disabled_still_allows_mr_diffs_via_project_read():
    d = full_decide(
        _intent("GET", "/projects/group%2Fproj/merge_requests/7/diffs"),
        StateView(),
        _cfg(),
        frozenset({"project.read"}),
    )
    assert d.allow and d.token == TokenKind.READ


# --- GraphQL: always denied, never proxied ------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/graphql"),
        ("POST", "/api/graphql"),
        ("PUT", "/api/graphql"),
        ("DELETE", "/api/graphql"),
        ("POST", "/api/graphql/whatever"),
        ("GET", "/api/graphql/whatever"),
    ],
)
def test_graphql_denied_on_every_method_and_path(method, path):
    intent = ApiIntent(_project="", _method=method, path=path, _host=HOST)
    d = full_decide(intent, StateView(), _cfg())
    assert not d.allow
    assert "unmodelled channel" in d.reason


# --- merge: denied by criticality, both wire shapes, regardless of config ----


def test_merge_denied_by_criticality_on_both_wire_shapes_even_with_everything_enabled():
    # Simulate "every configurable action enabled" — even the IRREVERSIBLE
    # ones a real deployment could never actually turn on through the loader.
    everything = frozenset(a.id for a in git_actions.ALL)

    via_merge_endpoint = _intent("PUT", "/projects/group%2Fproj/merge_requests/7/merge")
    d1 = full_decide(via_merge_endpoint, StateView(), _cfg(), everything)
    assert not d1.allow and "irreversible" in d1.reason

    via_state_event = _intent("PUT", "/projects/group%2Fproj/merge_requests/7", state_event="merge")
    via_state_event.mr_source_ok = True
    d2 = full_decide(via_state_event, StateView(), _cfg(), everything)
    assert not d2.allow and "irreversible" in d2.reason
