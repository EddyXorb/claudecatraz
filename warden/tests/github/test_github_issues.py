"""Every pull request is also an issue: issue-number writes resolve their kind
upstream, and a pull request keeps every pull-request check."""

from __future__ import annotations

import httpx
import pytest

from warden.core.config import Config, GitEndpoint, GitRules, HostCredentials

from .github_helpers import (
    ALL_GITHUB_ACTIONS,
    READ,
    REPO,
    UP,
    WRITE,
    endpoint,
    harness,
    make_cfg,
    pull_json,
)

ISSUE_ACTIONS = (
    "project.issue.create",
    "project.issue.edit",
    "project.issue.close",
    "project.issue.comment",
)
WITH_ISSUES = make_cfg(actions=ALL_GITHUB_ACTIONS)
IS_PULL = {"number": 7, "pull_request": {"url": "x"}}
IS_ISSUE = {"number": 7}

ISSUE_WRITES = [
    ("PATCH", f"{REPO}/issues/7", {"title": "t"}),
    ("PATCH", f"{REPO}/issues/7", {"state": "closed"}),
    ("PATCH", f"{REPO}/issues/7", {"state": "open"}),
    ("POST", f"{REPO}/issues/7/comments", {"body": "b"}),
]


@pytest.mark.parametrize("method,path,body", ISSUE_WRITES)
async def test_pull_number_gets_pull_actions_and_namespace_lookup(method, path, body):
    async with harness() as h:
        kind = h.mock.get(f"{UP}/issues/7").respond(200, json=IS_PULL)
        pull = h.mock.get(f"{UP}/pulls/7").respond(200, json=pull_json())
        fwd = h.forward_route()
        resp = await h.client.request(method, path, json=body)
        assert resp.status_code == 200
        assert kind.calls.last.request.headers["authorization"] == f"Bearer {READ}"
        assert pull.called
        assert fwd.calls.last.request.headers["authorization"] == f"Bearer {WRITE}"
        assert all(a.startswith("project.mr.") for a in h.last["actions"])
        assert h.last["kind"].startswith("mr_")


@pytest.mark.parametrize("method,path,body", ISSUE_WRITES)
@pytest.mark.parametrize(
    "pull", [pull_json(ref="main"), pull_json(head_id=5), pull_json(head_id=None)]
)
async def test_foreign_pull_via_issue_endpoint_denied(method, path, body, pull):
    async with harness() as h:
        h.mock.get(f"{UP}/issues/7").respond(200, json=IS_PULL)
        h.mock.get(f"{UP}/pulls/7").respond(200, json=pull)
        resp = await h.client.request(method, path, json=body)
        assert resp.status_code == 403
        assert "pull request is not on an agent branch" in resp.json()["reason"]


async def test_closing_a_pull_via_issues_needs_mr_close():
    actions = tuple(a for a in ALL_GITHUB_ACTIONS if a != "project.mr.close")
    async with harness(make_cfg(actions=actions)) as h:
        h.mock.get(f"{UP}/issues/7").respond(200, json=IS_PULL)
        resp = await h.client.patch(f"{REPO}/issues/7", json={"state": "closed"})
        assert resp.status_code == 403
        assert "project.mr.close not enabled" in resp.json()["reason"]


async def test_reopening_a_pull_via_issues_counts_against_ceiling():
    async with harness(make_cfg(rules=GitRules(max_open_mrs=1))) as h:
        h.ctx.guards[2].mr_state.replace_mrs("github.com", "octo/repo", [(1, "opened")])
        h.mock.get(f"{UP}/issues/7").respond(200, json=IS_PULL)
        h.mock.get(f"{UP}/pulls/7").respond(200, json=pull_json())
        resp = await h.client.patch(f"{REPO}/issues/7", json={"state": "open"})
        assert resp.status_code == 403 and "max open MRs" in resp.json()["reason"]


@pytest.mark.parametrize("method,path,body", ISSUE_WRITES)
async def test_plain_issue_denied_by_default(method, path, body):
    async with harness() as h:
        h.mock.get(f"{UP}/issues/7").respond(200, json=IS_ISSUE)
        resp = await h.client.request(method, path, json=body)
        assert resp.status_code == 403
        assert "project.issue." in resp.json()["reason"]
        assert "not enabled" in resp.json()["reason"]
        assert len(h.mock.calls) == 1  # only the kind lookup


@pytest.mark.parametrize(
    "method,path,body,action",
    [
        ("PATCH", f"{REPO}/issues/7", {"title": "t", "labels": ["x"]}, "project.issue.edit"),
        ("PATCH", f"{REPO}/issues/7", {"state": "closed"}, "project.issue.close"),
        ("POST", f"{REPO}/issues/7/comments", {"body": "b"}, "project.issue.comment"),
        ("POST", f"{REPO}/issues", {"title": "t"}, "project.issue.create"),
    ],
)
async def test_plain_issue_allowed_when_enabled_without_pull_lookup(method, path, body, action):
    async with harness(WITH_ISSUES) as h:
        h.mock.get(f"{UP}/issues/7").respond(200, json=IS_ISSUE)
        pull = h.mock.get(f"{UP}/pulls/7").respond(200, json=pull_json())
        fwd = h.forward_route()
        resp = await h.client.request(method, path, json=body)
        assert resp.status_code == 200
        assert not pull.called
        assert fwd.calls.last.request.headers["authorization"] == f"Bearer {WRITE}"
        assert h.last["actions"] == (action,)
        assert h.last["enabled_via"] == (action,)


async def test_issue_create_denied_by_default():
    async with harness() as h:
        resp = await h.client.post(f"{REPO}/issues", json={"title": "t"})
        assert resp.status_code == 403 and not h.mock.calls


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(410),
        httpx.Response(500),
        httpx.Response(200, content=b"<html>"),
        httpx.Response(200, json=[IS_PULL]),
    ],
)
@pytest.mark.parametrize("method,path,body", ISSUE_WRITES)
async def test_kind_lookup_failure_denies(method, path, body, response):
    async with harness(WITH_ISSUES) as h:
        h.mock.get(f"{UP}/issues/7").mock(return_value=response)
        resp = await h.client.request(method, path, json=body)
        assert resp.status_code == 403
        assert resp.json()["reason"] == "no recognized action for this request"
        assert len(h.mock.calls) == 1
        assert h.audit.events[-1].extra["issue_kind"] == "unknown"


async def test_unknown_kind_is_not_cached():
    async with harness(WITH_ISSUES) as h:
        kind = h.mock.get(f"{UP}/issues/7").mock(
            side_effect=[httpx.Response(500), httpx.Response(200, json=IS_ISSUE)]
        )
        h.forward_route()
        assert (await h.client.post(f"{REPO}/issues/7/comments", json={})).status_code == 403
        assert (await h.client.post(f"{REPO}/issues/7/comments", json={})).status_code == 200
        assert kind.call_count == 2


# --- no lookup before the kernel gates would have passed -------------------------------
async def test_no_lookup_for_non_allowed_project():
    async with harness(WITH_ISSUES) as h:
        resp = await h.client.patch("/api/v3/repos/evil/repo/issues/7", json={"state": "closed"})
        assert resp.status_code == 403 and "not in allowlist" in resp.json()["reason"]
        assert not h.mock.calls


async def test_no_lookup_for_unknown_host():
    async with harness(WITH_ISSUES, host="evil.example") as h:
        resp = await h.client.patch(f"{REPO}/issues/7", json={"state": "closed"})
        assert resp.status_code == 403 and "multi-target allowlist" in resp.json()["reason"]
        assert not h.mock.calls


async def test_no_lookup_for_read_only_host():
    cfg = make_cfg(creds={"github.com": HostCredentials(read_token=READ)})
    async with harness(cfg) as h:
        resp = await h.client.post(f"{REPO}/issues/7/comments", json={"body": "b"})
        assert resp.status_code == 403 and "write token unavailable" in resp.json()["reason"]
        assert not h.mock.calls


async def test_no_lookup_for_gitlab_host():
    cfg = Config(
        state_db_path=":memory:",
        git_endpoints=(
            GitEndpoint(host="gl.example", type="gitlab", allowed_projects=("octo/repo",)),
        ),
        git_credentials={"gl.example": HostCredentials(read_token=READ, write_token=WRITE)},
    )
    async with harness(cfg, host="gl.example") as h:
        resp = await h.client.patch(f"{REPO}/issues/7", json={"state": "closed"})
        assert resp.status_code == 403 and "not served by the github guard" in resp.json()["reason"]
        assert not h.mock.calls


async def test_no_lookup_for_closed_github_host():
    cfg = make_cfg(
        endpoint(), endpoint("ghe.example"), creds={"github.com": HostCredentials(READ, WRITE)}
    )
    async with harness(cfg, host="ghe.example") as h:
        resp = await h.client.patch(f"{REPO}/issues/7", json={"state": "closed"})
        assert resp.status_code == 403 and not h.mock.calls


async def test_no_lookup_for_issue_reads():
    async with harness() as h:
        fwd = h.forward_route()
        assert (await h.client.get(f"{REPO}/issues/7")).status_code == 200
        assert (await h.client.get(f"{REPO}/issues/7/comments")).status_code == 200
        assert len(h.mock.calls) == 2 == len(fwd.calls)


async def test_issue_kind_audited_only_on_issue_rows():
    async with harness() as h:
        h.mock.get(f"{UP}/issues/7").respond(200, json=IS_PULL)
        h.mock.get(f"{UP}/pulls/7").respond(200, json=pull_json())
        h.forward_route()
        await h.client.post(f"{REPO}/issues/7/comments", json={"body": "b"})
        assert h.audit.events[-1].extra["issue_kind"] == "pull"
        await h.client.post(f"{REPO}/pulls/7/comments", json={"body": "b"})
        assert "issue_kind" not in h.audit.events[-1].extra


async def test_issue_kind_reaches_the_written_audit_record():
    async with harness() as h:
        h.mock.get(f"{UP}/issues/7").respond(200, json=IS_PULL)
        h.mock.get(f"{UP}/pulls/7").respond(200, json=pull_json())
        h.forward_route()
        await h.client.post(f"{REPO}/issues/7/comments", json={"body": "b"})
        assert h.last["issue_kind"] == "pull"
