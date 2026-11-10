"""End-to-end GitHub guard behaviour through the app: every catalog row,
token injection, header allowlist, irreversible actions, scope lookups."""

from __future__ import annotations

import json

import httpx
import pytest

from warden.core.config import GitRules
from warden.guards.git.github.recognizers import CATALOG

from .github_helpers import (
    ALL_GITHUB_ACTIONS,
    API,
    READ,
    REPO,
    UP,
    WRITE,
    harness,
    make_cfg,
    mock_owned_lookups,
    pull_json,
    run_json,
)

# row id -> (method, agent path, JSON body or None, expected action ids)
PROBES = {
    "pull.create": (
        "POST",
        f"{REPO}/pulls",
        {"head": "claude/x", "base": "main", "title": "t"},
        {"project.mr.create"},
    ),
    "pull.update": ("PATCH", f"{REPO}/pulls/7", {"title": "t"}, {"project.mr.edit"}),
    "pull.merge": ("PUT", f"{REPO}/pulls/7/merge", {}, {"project.mr.merge"}),
    "pull.review_comment": (
        "POST",
        f"{REPO}/pulls/7/comments",
        {"body": "b"},
        {"project.mr.comment"},
    ),
    "pull.review_comment_reply": (
        "POST",
        f"{REPO}/pulls/7/comments/3/replies",
        {"body": "b"},
        {"project.mr.comment"},
    ),
    "pull.review": (
        "POST",
        f"{REPO}/pulls/7/reviews",
        {"event": "COMMENT", "body": "b"},
        {"project.mr.comment"},
    ),
    "pull.review_submit": (
        "POST",
        f"{REPO}/pulls/7/reviews/5/events",
        {"event": "COMMENT"},
        {"project.mr.comment"},
    ),
    "issue.create": ("POST", f"{REPO}/issues", {"title": "t"}, {"project.issue.create"}),
    "issue.update": ("PATCH", f"{REPO}/issues/7", {"state": "closed"}, {"project.mr.close"}),
    "issue.comment": ("POST", f"{REPO}/issues/7/comments", {"body": "b"}, {"project.mr.comment"}),
    "workflow.dispatch": (
        "POST",
        f"{REPO}/actions/workflows/ci.yml/dispatches",
        {"ref": "claude/x"},
        {"project.ci.trigger"},
    ),
    "run.rerun": ("POST", f"{REPO}/actions/runs/9/rerun", None, {"project.ci.trigger"}),
    "run.rerun_failed": (
        "POST",
        f"{REPO}/actions/runs/9/rerun-failed-jobs",
        None,
        {"project.ci.trigger"},
    ),
    "run.cancel": ("POST", f"{REPO}/actions/runs/9/cancel", None, {"project.ci.trigger"}),
    "job.rerun": ("POST", f"{REPO}/actions/jobs/11/rerun", None, {"project.ci.trigger"}),
    "ref.create": (
        "POST",
        f"{REPO}/git/refs",
        {"ref": "refs/heads/claude/x", "sha": "a" * 40},
        {"repo.branch.create"},
    ),
    "ref.delete": ("DELETE", f"{REPO}/git/refs/heads/claude/x", None, {"repo.branch.delete"}),
    "read.contents": ("GET", f"{REPO}/contents/README.md", None, {"repo.read"}),
    "read.git": ("GET", f"{REPO}/git/trees/main", None, {"repo.read"}),
    "read.commits": ("GET", f"{REPO}/commits", None, {"repo.read"}),
    "read.branches": ("GET", f"{REPO}/branches/main", None, {"repo.read"}),
    "read.tags": ("GET", f"{REPO}/tags", None, {"repo.read"}),
    "read.compare": ("GET", f"{REPO}/compare/main...claude/x", None, {"repo.read"}),
    "read.tarball": ("GET", f"{REPO}/tarball/main", None, {"repo.read"}),
    "read.zipball": ("GET", f"{REPO}/zipball", None, {"repo.read"}),
    "read.readme": ("GET", f"{REPO}/readme", None, {"repo.read"}),
    "read.artifacts": ("GET", f"{REPO}/actions/artifacts/3/zip", None, {"repo.read"}),
    "read.search": ("GET", "/api/v3/search/issues?q=x", None, {"instance.projects.read"}),
    "read.repositories": ("GET", "/api/v3/repositories", None, {"instance.projects.read"}),
    "read.orgs": ("GET", "/api/v3/orgs/octo/repos", None, {"instance.projects.read"}),
    "read.organizations": ("GET", "/api/v3/organizations", None, {"instance.projects.read"}),
    "read.issues": ("GET", "/api/v3/issues", None, {"instance.projects.read"}),
    "read.users": ("GET", "/api/v3/users/octo", None, {"instance.users.read"}),
    "read.user": ("GET", "/api/v3/user", None, {"instance.users.read"}),
    "read.events": ("GET", "/api/v3/events", None, {"instance.users.read"}),
    "read.meta": ("GET", "/api/v3/meta", None, {"instance.meta.read"}),
    "read.rate_limit": ("GET", "/api/v3/rate_limit", None, {"instance.meta.read"}),
    "read.versions": ("GET", "/api/v3/versions", None, {"instance.meta.read"}),
    "read.project": ("GET", f"{REPO}/pulls", None, {"project.read"}),
}
IRREVERSIBLE = {"pull.merge", "ref.delete"}


def test_every_catalog_row_has_a_probe():
    assert set(PROBES) == {row.id for row in CATALOG}


def _upstream_path(agent_path: str) -> str:
    return API + agent_path.removeprefix("/api/v3")


async def _send(h, method, path, body, **kw):
    content = None if body is None else json.dumps(body).encode()
    headers = {"content-type": "application/json", **kw.pop("headers", {})}
    return await h.client.request(method, path, content=content, headers=headers, **kw)


def _no_secret(resp, h):
    blob = resp.text + json.dumps(dict(resp.headers)) + json.dumps(h.audit.records, default=str)
    assert "SECRET" not in blob


@pytest.mark.parametrize("row_id", sorted(PROBES))
async def test_catalog_row_behaviour(row_id):
    method, path, body, expected = PROBES[row_id]
    async with harness(make_cfg(actions=ALL_GITHUB_ACTIONS)) as h:
        mock_owned_lookups(h.mock)
        fwd = h.forward_route()
        resp = await _send(h, method, path, body)
        assert set(h.last["actions"]) == expected
        _no_secret(resp, h)
        if row_id in IRREVERSIBLE:
            assert resp.status_code == 403
            assert "irreversible" in resp.json()["reason"]
            assert not h.mock.calls
            return
        assert resp.status_code == 200, resp.text
        forwarded = fwd.calls.last.request
        assert forwarded.method == method
        assert str(forwarded.url) == _upstream_path(path)
        token = READ if method == "GET" else WRITE
        assert forwarded.headers["authorization"] == f"Bearer {token}"
        if body is not None:
            assert json.loads(forwarded.content) == body
        # every lookup uses the read token
        for call in h.mock.calls:
            if call.request is not forwarded:
                assert call.request.method == "GET"
                assert call.request.headers["authorization"] == f"Bearer {READ}"


async def test_writes_are_recorded_per_host_and_reads_are_not():
    async with harness() as h:
        h.forward_route()
        await _send(h, "POST", f"{REPO}/pulls", {"head": "claude/x"})
        await h.client.get(f"{REPO}/pulls")
        assert h.state.writes_last_hour("github.com") == 1


# --- header allowlist --------------------------------------------------------------
async def test_agent_headers_outside_the_allowlist_never_reach_upstream():
    async with harness() as h:
        fwd = h.forward_route()
        resp = await h.client.post(
            f"{REPO}/pulls",
            content=b'{"head": "claude/x"}',
            headers={
                "Authorization": "Bearer AGENT",
                "PRIVATE-TOKEN": "AGENT",
                "X-HTTP-Method-Override": "PUT",
                "X-Method-Override": "DELETE",
                "Cookie": "user_session=AGENT",
                "X-Forwarded-For": "1.2.3.4",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "If-None-Match": '"etag"',
                "User-Agent": "agent-ua",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        sent = fwd.calls.last.request.headers
        assert sent["authorization"] == f"Bearer {WRITE}"
        assert "AGENT" not in json.dumps(dict(sent))
        for name in (
            "private-token",
            "x-http-method-override",
            "x-method-override",
            "cookie",
            "x-forwarded-for",
        ):
            assert name not in sent
        assert sent["accept"] == "application/vnd.github+json"
        assert sent["x-github-api-version"] == "2022-11-28"
        assert sent["if-none-match"] == '"etag"'
        assert sent["user-agent"] == "agent-ua"
        assert fwd.calls.last.request.method == "POST"


async def test_method_override_on_a_read_stays_a_read():
    async with harness() as h:
        fwd = h.forward_route()
        resp = await h.client.get(
            f"{REPO}/pulls/7/merge", headers={"X-HTTP-Method-Override": "PUT"}
        )
        assert resp.status_code == 200
        req = fwd.calls.last.request
        assert req.method == "GET" and "x-http-method-override" not in req.headers
        assert req.headers["authorization"] == f"Bearer {READ}"


async def test_get_body_is_not_forwarded_and_query_is():
    async with harness() as h:
        fwd = h.forward_route()
        await h.client.request("GET", f"{REPO}/contents/a?ref=claude/x", content=b'{"x": 1}')
        req = fwd.calls.last.request
        assert req.content == b""
        assert str(req.url) == f"{UP}/contents/a?ref=claude/x"


async def test_upstream_response_is_relayed_without_hop_headers():
    async with harness() as h:
        h.mock.get(f"{UP}/pulls").respond(
            404, json={"message": "Not Found"}, headers={"x-ratelimit-remaining": "9"}
        )
        resp = await h.client.get(f"{REPO}/pulls")
        assert resp.status_code == 404
        assert resp.json() == {"message": "Not Found"}
        assert resp.headers["x-ratelimit-remaining"] == "9"


# --- irreversible ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("PUT", f"{REPO}/pulls/7/merge", {"merge_method": "squash"}),
        ("PUT", f"{REPO}/pulls/7/merge/", None),
        ("PUT", f"{REPO}/pulls/007/merge", None),
        ("DELETE", f"{REPO}/git/refs/heads/claude/x", None),
        ("DELETE", f"{REPO}/git/refs/tags/v1", None),
        ("POST", f"{REPO}/git/refs", {"ref": "refs/tags/v1", "sha": "a"}),
        ("POST", f"{REPO}/git/refs", {"ref": "refs/tags/claude/x", "sha": "a"}),
    ],
)
async def test_irreversible_always_403_without_upstream_call(method, path, body):
    # Even an endpoint that lists every action cannot enable an irreversible one.
    async with harness(make_cfg(actions=ALL_GITHUB_ACTIONS)) as h:
        resp = await _send(h, method, path, body)
        assert resp.status_code == 403
        assert "irreversible" in resp.json()["reason"]
        assert not h.mock.calls


async def test_merge_denied_even_when_locked_and_unreconciled():
    async with harness(reconciled=False) as h:
        resp = await h.client.put(f"{REPO}/pulls/7/merge")
        assert resp.status_code == 403 and "irreversible" in resp.json()["reason"]
        assert not h.mock.calls


# --- refs ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ref,ok",
    [
        ("refs/heads/claude/x", True),
        ("refs/heads/main", False),
        ("refs/heads/claudex", False),
        ("refs/pull/1/head", False),
        ("refs/remotes/origin/claude/x", False),
        ("claude/x", False),
    ],
)
async def test_ref_create(ref, ok):
    async with harness() as h:
        fwd = h.forward_route()
        resp = await _send(h, "POST", f"{REPO}/git/refs", {"ref": ref, "sha": "a" * 40})
        assert (resp.status_code == 200) is ok
        assert bool(fwd.calls) is ok


@pytest.mark.parametrize(
    "body", [{"ref": 5, "sha": "a"}, {"ref": "refs/heads/claude/x", "force": True}]
)
async def test_ref_create_bad_body_denied(body):
    async with harness() as h:
        resp = await _send(h, "POST", f"{REPO}/git/refs", body)
        assert resp.status_code == 403 and not h.mock.calls


@pytest.mark.parametrize(
    "method,path",
    [
        ("PATCH", f"{REPO}/git/refs/heads/claude/x"),
        ("PATCH", f"{REPO}/git/refs/heads/main"),
        ("DELETE", f"{REPO}/git/refs/pull/1/head"),
        ("PUT", f"{REPO}/contents/README.md"),
        ("DELETE", f"{REPO}/contents/README.md"),
        ("POST", f"{REPO}/merges"),
        ("POST", f"{REPO}/dispatches"),
        ("DELETE", REPO),
        ("PATCH", REPO),
        ("POST", f"{REPO}/forks"),
        ("PUT", f"{REPO}/collaborators/evil"),
        ("POST", f"{REPO}/hooks"),
        ("PUT", f"{REPO}/actions/secrets/X"),
        ("PUT", f"{REPO}/branches/main/protection"),
        ("PUT", f"{REPO}/pulls/7/update-branch"),
        ("PUT", f"{REPO}/pulls/7"),
        ("DELETE", f"{REPO}/pulls/7/reviews/5"),
        ("POST", f"{REPO}/pulls/7/requested_reviewers"),
        ("POST", f"{REPO}/actions/runs/9/approve"),
        ("POST", f"{REPO}/actions/runs/9/force-cancel"),
        ("POST", f"{REPO}/git/commits"),
        ("POST", f"{REPO}/releases"),
        ("POST", "/api/v3/user/repos"),
        ("POST", f"{REPO}/pulls/abc/comments"),
        ("POST", f"{REPO}//pulls"),
        ("POST", f"{REPO}/%70ulls"),
    ],
)
async def test_unmodelled_writes_denied_without_upstream_call(method, path):
    async with harness(make_cfg(actions=ALL_GITHUB_ACTIONS)) as h:
        resp = await _send(h, method, path, {"ref": "refs/heads/claude/x", "sha": "a"})
        assert resp.status_code == 403
        assert not h.mock.calls


# --- pull request create ------------------------------------------------------------
@pytest.mark.parametrize(
    "head,ok",
    [
        ("claude/x", True),
        ("octo:claude/x", True),
        ("OCTO:claude/x", True),
        ("evil:claude/x", False),
        ("octo-fork:claude/x", False),
        ("main", False),
        ("octo:main", False),
        ("", False),
    ],
)
async def test_pull_create_head(head, ok):
    async with harness() as h:
        fwd = h.forward_route()
        resp = await _send(h, "POST", f"{REPO}/pulls", {"head": head, "base": "main"})
        assert (resp.status_code == 200) is ok
        assert bool(fwd.calls) is ok


@pytest.mark.parametrize(
    "content",
    [
        b'{"base": "main"}',
        b'{"head": 1}',
        b'{"head": ["claude/x"]}',
        b'{"head": null}',
        b'{"head": "claude/x", "issue": 3}',
        b'{"head": "claude/x", "head_repo": "evil/repo"}',
        b'{"head": "claude/x", "Head": "main"}',
        b'{"head": "claude/x", "head": "main"}',
        b'{"head": "main", "he\\u0061d": "claude/x"}',
        b'[{"head": "claude/x"}]',
        b'{"head": "claude/x"',
        b"head=claude/x&base=main",
        b'"claude/x"',
    ],
)
async def test_pull_create_undecidable_body_denied(content):
    async with harness() as h:
        resp = await h.client.post(
            f"{REPO}/pulls", content=content, headers={"content-type": "application/json"}
        )
        assert resp.status_code == 403
        assert not h.mock.calls


async def test_pull_create_form_encoded_denied():
    async with harness() as h:
        resp = await h.client.post(f"{REPO}/pulls", data={"head": "claude/x", "base": "main"})
        assert resp.status_code == 403 and not h.mock.calls


async def test_write_with_query_string_denied():
    async with harness() as h:
        resp = await h.client.post(f"{REPO}/pulls?head=main", json={"head": "claude/x"})
        assert resp.status_code == 403
        assert "no recognized action" in resp.json()["reason"]
        assert not h.mock.calls


async def test_pull_create_open_mr_ceiling():
    async with harness(make_cfg(rules=GitRules(max_open_mrs=1))) as h:
        h.ctx.guards[2].mr_state.replace_mrs("github.com", "octo/repo", [(1, "opened")])
        resp = await _send(h, "POST", f"{REPO}/pulls", {"head": "claude/x"})
        assert resp.status_code == 403 and "max open MRs reached (1)" in resp.json()["reason"]
        assert not h.mock.calls


async def test_pull_create_rate_limit():
    async with harness(make_cfg(rules=GitRules(max_writes_per_hour=1))) as h:
        h.forward_route()
        assert (await _send(h, "POST", f"{REPO}/pulls", {"head": "claude/x"})).status_code == 200
        resp = await _send(h, "POST", f"{REPO}/pulls", {"head": "claude/y"})
        assert resp.status_code == 403 and "rate limit reached (1/h)" in resp.json()["reason"]


async def test_pull_create_denied_while_locked():
    async with harness(reconciled=False) as h:
        resp = await _send(h, "POST", f"{REPO}/pulls", {"head": "claude/x"})
        assert resp.status_code == 403 and "state locked" in resp.json()["reason"]
        assert not h.mock.calls


async def test_reads_pass_while_locked():
    async with harness(reconciled=False) as h:
        h.forward_route()
        assert (await h.client.get(f"{REPO}/pulls")).status_code == 200


async def test_custom_branch_prefix_applies():
    async with harness(make_cfg(rules=GitRules(branch_prefixes=("bot/",)))) as h:
        h.forward_route()
        assert (await _send(h, "POST", f"{REPO}/pulls", {"head": "bot/x"})).status_code == 200
        assert (await _send(h, "POST", f"{REPO}/pulls", {"head": "claude/x"})).status_code == 403


# --- pull request update / comments / reviews ---------------------------------------------
PULL_WRITES = [
    ("PATCH", f"{REPO}/pulls/7", {"title": "t"}),
    ("PATCH", f"{REPO}/pulls/7", {"state": "closed"}),
    ("POST", f"{REPO}/pulls/7/comments", {"body": "b"}),
    ("POST", f"{REPO}/pulls/7/comments/3/replies", {"body": "b"}),
    ("POST", f"{REPO}/pulls/7/reviews", {"body": "b"}),
    ("POST", f"{REPO}/pulls/7/reviews/5/events", {"event": "COMMENT"}),
]


@pytest.mark.parametrize(
    "pull,reason",
    [
        (pull_json(ref="main"), "pull request is not on an agent branch of this repository"),
        (pull_json(head_id=99), "pull request is not on an agent branch of this repository"),
        (pull_json(head_id=None), "pull request is not on an agent branch of this repository"),
        ([pull_json()], "pull request is not on an agent branch of this repository"),
    ],
)
@pytest.mark.parametrize("method,path,body", PULL_WRITES)
async def test_pull_writes_denied_for_foreign_pulls(method, path, body, pull, reason):
    async with harness() as h:
        lookup = h.mock.get(f"{UP}/pulls/7").respond(200, json=pull)
        resp = await _send(h, method, path, body)
        assert resp.status_code == 403 and resp.json()["reason"] == reason
        assert h.mock.calls.last.request is lookup.calls.last.request


@pytest.mark.parametrize(
    "response",
    [httpx.Response(404), httpx.Response(500), httpx.Response(200, content=b"not json")],
)
@pytest.mark.parametrize("method,path,body", PULL_WRITES)
async def test_pull_writes_denied_when_lookup_fails(method, path, body, response):
    async with harness() as h:
        h.mock.get(f"{UP}/pulls/7").mock(return_value=response)
        resp = await _send(h, method, path, body)
        assert resp.status_code == 403
        assert resp.json()["reason"] == "pull request could not be verified"
        assert len(h.mock.calls) == 1


@pytest.mark.parametrize("method,path,body", PULL_WRITES)
async def test_pull_writes_allowed_for_agent_pulls(method, path, body):
    async with harness() as h:
        h.mock.get(f"{UP}/pulls/7").respond(200, json=pull_json())
        fwd = h.forward_route()
        resp = await _send(h, method, path, body)
        assert resp.status_code == 200
        assert fwd.calls.last.request.headers["authorization"] == f"Bearer {WRITE}"


@pytest.mark.parametrize(
    "path,event,ok",
    [
        ("reviews", None, True),
        ("reviews", "COMMENT", True),
        ("reviews", "APPROVE", False),
        ("reviews", "REQUEST_CHANGES", False),
        ("reviews", "approve", False),
        ("reviews/5/events", None, False),
        ("reviews/5/events", "COMMENT", True),
        ("reviews/5/events", "APPROVE", False),
        ("reviews/5/events", "REQUEST_CHANGES", False),
    ],
)
async def test_review_events(path, event, ok):
    async with harness() as h:
        h.mock.get(f"{UP}/pulls/7").respond(200, json=pull_json())
        fwd = h.forward_route()
        body = {"body": "b"} if event is None else {"body": "b", "event": event}
        resp = await _send(h, "POST", f"{REPO}/pulls/7/{path}", body)
        assert (resp.status_code == 200) is ok
        assert bool(fwd.calls) is ok
        if not ok:
            assert not h.mock.calls  # denied before any lookup


async def test_review_event_non_string_denied():
    async with harness() as h:
        resp = await _send(h, "POST", f"{REPO}/pulls/7/reviews", {"event": ["COMMENT"]})
        assert resp.status_code == 403 and not h.mock.calls


@pytest.mark.parametrize("state", ["merged", "OPEN", 1])
async def test_pull_update_unknown_state_denied(state):
    async with harness() as h:
        resp = await _send(h, "PATCH", f"{REPO}/pulls/7", {"state": state})
        assert resp.status_code == 403 and not h.mock.calls


async def test_pull_update_disallowed_key_denied():
    async with harness() as h:
        resp = await _send(h, "PATCH", f"{REPO}/pulls/7", {"title": "t", "head": "main"})
        assert resp.status_code == 403 and not h.mock.calls


async def test_pull_reopen_counts_against_open_mr_ceiling():
    async with harness(make_cfg(rules=GitRules(max_open_mrs=1))) as h:
        h.ctx.guards[2].mr_state.replace_mrs("github.com", "octo/repo", [(1, "opened")])
        h.mock.get(f"{UP}/pulls/7").respond(200, json=pull_json())
        fwd = h.forward_route()
        resp = await _send(h, "PATCH", f"{REPO}/pulls/7", {"state": "open"})
        assert resp.status_code == 403 and "max open MRs" in resp.json()["reason"]
        resp = await _send(h, "PATCH", f"{REPO}/pulls/7", {"state": "closed"})
        assert resp.status_code == 200 and len(fwd.calls) == 1


async def test_pull_close_disabled_per_endpoint_denied_before_lookup():
    actions = tuple(a for a in ALL_GITHUB_ACTIONS if a != "project.mr.close")
    async with harness(make_cfg(actions=actions)) as h:
        resp = await _send(h, "PATCH", f"{REPO}/pulls/7", {"state": "closed"})
        assert resp.status_code == 403
        assert "project.mr.close not enabled" in resp.json()["reason"]
        assert not h.mock.calls


# --- CI ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "body,ok",
    [
        ({"ref": "claude/x"}, True),
        ({"ref": "refs/heads/claude/x"}, True),
        ({"ref": "claude/x", "inputs": {"a": "b"}}, True),
        ({"ref": "refs/tags/claude/x"}, False),
        ({"ref": "refs/heads/main"}, False),
        ({"ref": "main"}, False),
        ({"ref": "refs/pull/1/merge"}, False),
        ({}, False),
        ({"ref": 1}, False),
        ({"ref": "claude/x", "extra": 1}, False),
    ],
)
async def test_workflow_dispatch(body, ok):
    async with harness() as h:
        fwd = h.forward_route()
        resp = await _send(h, "POST", f"{REPO}/actions/workflows/ci.yml/dispatches", body)
        assert (resp.status_code == 200) is ok
        assert bool(fwd.calls) is ok


RUN_WRITES = ["rerun", "rerun-failed-jobs", "cancel"]


@pytest.mark.parametrize("verb", RUN_WRITES)
@pytest.mark.parametrize(
    "run,ok",
    [
        (run_json(), True),
        (run_json(branch="main"), False),
        (run_json(head_id=2), False),
        ({"head_branch": "claude/x", "repository": {"id": 1}}, False),
    ],
)
async def test_run_writes_by_run_ownership(verb, run, ok):
    async with harness() as h:
        h.mock.get(f"{UP}/actions/runs/9").respond(200, json=run)
        fwd = h.forward_route()
        resp = await h.client.post(f"{REPO}/actions/runs/9/{verb}")
        assert (resp.status_code == 200) is ok
        assert bool(fwd.calls) is ok
        if not ok:
            assert "workflow run is not on an agent branch" in resp.json()["reason"]


@pytest.mark.parametrize("verb", RUN_WRITES)
async def test_run_writes_denied_when_lookup_fails(verb):
    async with harness() as h:
        h.mock.get(f"{UP}/actions/runs/9").respond(404)
        resp = await h.client.post(f"{REPO}/actions/runs/9/{verb}")
        assert resp.json()["reason"] == "workflow run could not be verified"


async def test_job_rerun_resolves_its_run():
    async with harness() as h:
        job = h.mock.get(f"{UP}/actions/jobs/11").respond(200, json={"run_id": 9})
        run = h.mock.get(f"{UP}/actions/runs/9").respond(200, json=run_json())
        fwd = h.forward_route()
        resp = await h.client.post(f"{REPO}/actions/jobs/11/rerun")
        assert resp.status_code == 200
        assert job.called and run.called
        assert str(fwd.calls.last.request.url) == f"{UP}/actions/jobs/11/rerun"


@pytest.mark.parametrize(
    "job_resp,run_resp,reason",
    [
        (httpx.Response(404), None, "could not be verified"),
        (httpx.Response(200, json={}), None, "could not be verified"),
        (httpx.Response(200, json={"run_id": "9"}), None, "could not be verified"),
        (httpx.Response(200, json={"run_id": True}), None, "could not be verified"),
        (httpx.Response(200, json=[9]), None, "could not be verified"),
        (httpx.Response(200, json={"run_id": 9}), httpx.Response(500), "could not be verified"),
        (
            httpx.Response(200, json={"run_id": 9}),
            httpx.Response(200, json=run_json("main")),
            "is not on an agent branch",
        ),
    ],
)
async def test_job_rerun_denied(job_resp, run_resp, reason):
    async with harness() as h:
        h.mock.get(f"{UP}/actions/jobs/11").mock(return_value=job_resp)
        if run_resp is not None:
            h.mock.get(f"{UP}/actions/runs/9").mock(return_value=run_resp)
        resp = await h.client.post(f"{REPO}/actions/jobs/11/rerun")
        assert resp.status_code == 403
        assert resp.json()["reason"].startswith("workflow job") and reason in resp.json()["reason"]


async def test_ci_trigger_disabled_denies_without_lookup():
    actions = tuple(a for a in ALL_GITHUB_ACTIONS if a != "project.ci.trigger")
    async with harness(make_cfg(actions=actions)) as h:
        resp = await h.client.post(f"{REPO}/actions/runs/9/rerun")
        assert resp.status_code == 403 and not h.mock.calls


# --- lookup transport failure ----------------------------------------------------------
async def test_lookup_transport_error_denies_with_403_and_audit():
    async with harness(raise_app_exceptions=False) as h:
        h.mock.get(f"{UP}/pulls/7").mock(side_effect=httpx.ConnectError("down"))
        resp = await _send(h, "POST", f"{REPO}/pulls/7/comments", {"body": "b"})
        assert resp.status_code == 403
        assert h.audit.records and h.last["decision"] == "deny"
