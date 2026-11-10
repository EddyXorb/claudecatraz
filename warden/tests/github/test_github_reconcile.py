"""Reconcile, Link pagination, lookup cache, /policy report, config and audit."""

from __future__ import annotations

import httpx
import pytest
import respx
from git_wire import INFO_REFS, advertisement_response

from warden.app import create_admin_app
from warden.context import build_context
from warden.core.audit import AuditLog
from warden.core.config import Config, GitEndpoint, HostCredentials
from warden.core.state import State
from warden.core.transport import UpstreamRouter
from warden.guards.git import actions as git_actions
from warden.guards.git.endpoints import upstream_layout
from warden.guards.git.github import actions as github_actions
from warden.guards.git.github.guard import GithubGuard
from warden.guards.git.github.intent import IssueKind
from warden.guards.git.github.lookup import GithubLookups
from warden.guards.git.github.recognizers import CATALOG
from warden.guards.git.github.reconcile import get_linked_pages, reconcile_pulls
from warden.guards.git.mr_state import MrState

from .github_helpers import (
    ALL_GITHUB_ACTIONS,
    API,
    READ,
    REPO,
    UP,
    WRITE,
    endpoint,
    harness,
    make_cfg,
    pull_json,
)

PULLS = rf"{UP}/pulls\?state=open&per_page=100"


def _guard(cfg: Config) -> GithubGuard:
    return GithubGuard(cfg, State(":memory:"), AuditLog("-"), UpstreamRouter(cfg, upstream_layout))


def _next(url: str) -> dict[str, str]:
    return {"Link": f'<{url}>; rel="next", <{url}&last=1>; rel="last"'}


# --- reconcile ---------------------------------------------------------------------
async def test_reconcile_follows_link_pagination_and_counts_only_agent_pulls():
    g = _guard(make_cfg())
    with respx.mock() as mock:
        page1 = mock.get(url__regex=PULLS).respond(
            200,
            json=[pull_json(number=1), pull_json(ref="main", number=2)],
            headers=_next(f"{API}/repositories/1/pulls?state=open&page=2"),
        )
        page2 = mock.get(f"{API}/repositories/1/pulls?state=open&page=2").respond(
            200,
            json=[
                pull_json(number=3),
                pull_json(head_id=9, number=4),  # fork with a namespace branch name
                pull_json(head_id=None, number=5),  # deleted fork
            ],
        )
        assert await g.reconcile() is True
        assert page1.calls.last.request.headers["authorization"] == f"Bearer {READ}"
        assert page2.calls.last.request.headers["authorization"] == f"Bearer {READ}"
    assert g.mr_state.open_mrs("github.com") == 2
    assert g.state.is_reconciled("github")
    assert g.state_view("github.com").open_mrs == 2


async def test_reconcile_replaces_stale_rows():
    g = _guard(make_cfg())
    g.mr_state.replace_mrs("github.com", "octo/repo", [(1, "opened"), (2, "opened")])
    with respx.mock() as mock:
        mock.get(url__regex=PULLS).respond(200, json=[])
        assert await g.reconcile()
    assert g.mr_state.open_mrs("github.com") == 0


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example/repos/octo/repo/pulls?page=2",
        "https://api.github.com.evil.example/x?page=2",
        "http://api.github.com/repositories/1/pulls?page=2",
        "/repositories/1/pulls?page=2",
    ],
)
async def test_foreign_next_link_fails_reconcile_and_never_carries_the_token(next_url):
    g = _guard(make_cfg())
    with respx.mock(assert_all_mocked=False) as mock:
        mock.get(url__regex=PULLS).respond(200, json=[pull_json()], headers=_next(next_url))
        assert await g.reconcile() is False
        assert len(mock.calls) == 1
    assert not g.state.is_reconciled("github")
    assert g.state_view("github.com").locked


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"items": []}),
        httpx.Response(200, json=None),
        httpx.Response(500),
        httpx.Response(401),
        httpx.Response(
            200,
            json=[{"head": {"ref": "claude/x", "repo": {"id": 1}}, "base": {"repo": {"id": 1}}}],
        ),
    ],
)
async def test_bad_pull_listing_fails_reconcile(response):
    g = _guard(make_cfg())
    with respx.mock() as mock:
        mock.get(url__regex=PULLS).mock(return_value=response)
        assert await g.reconcile() is False
    assert not g.state.is_reconciled("github")


async def test_one_failing_project_keeps_the_guard_locked_but_counts_the_others():
    cfg = make_cfg(endpoint(allowed_projects=("octo/repo", "octo/two")))
    g = _guard(cfg)
    with respx.mock() as mock:
        mock.get(url__regex=PULLS).respond(200, json=[pull_json()])
        mock.get(url__regex=rf"{API}/repos/octo/two/pulls\?.*").respond(500)
        assert await g.reconcile() is False
    assert g.mr_state.open_mrs("github.com") == 1


async def test_reconcile_skips_closed_and_foreign_type_hosts():
    cfg = Config(
        state_db_path=":memory:",
        git_endpoints=(
            endpoint(),
            endpoint("closed.example"),
            GitEndpoint(host="gl.example", type="gitlab", allowed_projects=("g/p",)),
        ),
        git_credentials={
            "github.com": HostCredentials(READ, WRITE),
            "gl.example": HostCredentials(READ, WRITE),
        },
    )
    router = UpstreamRouter(cfg, upstream_layout)
    mr = MrState(State(":memory:").store)
    with respx.mock() as mock:
        mock.get(url__regex=PULLS).respond(200, json=[pull_json()])
        assert await reconcile_pulls(cfg, router, mr) is True
        assert all("api.github.com" in str(c.request.url) for c in mock.calls)
    assert mr.open_mrs("github.com") == 1


async def test_reconcile_with_no_github_hosts_unlocks():
    cfg = Config(
        git_endpoints=(GitEndpoint(host="gl.example", type="gitlab"),),
        git_credentials={"gl.example": HostCredentials(READ, WRITE)},
    )
    g = _guard(cfg)
    with respx.mock():
        assert await g.reconcile() is True
    assert g.state.is_reconciled("github")


async def test_ghes_pagination_stays_under_its_rest_base():
    cfg = make_cfg(endpoint("ghe.example"))
    router = UpstreamRouter(cfg, upstream_layout)
    up = router.for_host("ghe.example")
    with respx.mock() as mock:
        mock.get("https://ghe.example/api/v3/a").respond(
            200, json=[1], headers=_next("https://ghe.example/api/v3/b?p=2")
        )
        mock.get("https://ghe.example/api/v3/b?p=2").respond(200, json=[2])
        assert await get_linked_pages(up, "a") == [1, 2]
        with pytest.raises(ValueError, match="leaves the REST base"):
            mock.get("https://ghe.example/api/v3/c").respond(
                200, json=[], headers=_next("https://ghe.example/other/x")
            )
            await get_linked_pages(up, "c")
    await router.aclose()


async def test_mixed_case_host_paginates():
    cfg = make_cfg(endpoint("GitHub.com"), creds={"github.com": HostCredentials(READ, WRITE)})
    g = _guard(cfg)
    with respx.mock() as mock:
        mock.get(url__regex=r"https://api\.github\.com/repos/octo/repo/pulls\?.*").respond(
            200, json=[], headers=_next(f"{API}/repositories/1/pulls?page=2")
        )
        mock.get(f"{API}/repositories/1/pulls?page=2").respond(200, json=[])
        assert await g.reconcile() is True


async def test_reconcile_all_with_gitlab_and_github_unlocks_every_guard():
    cfg = Config(
        state_db_path=":memory:",
        git_endpoints=(
            GitEndpoint(host="gl.example", type="gitlab", allowed_projects=("g/p",)),
            endpoint(),
        ),
        git_credentials={
            "gl.example": HostCredentials(READ, WRITE),
            "github.com": HostCredentials(READ, WRITE),
        },
    )
    st = State(":memory:")
    ctx = build_context(cfg, st, AuditLog("-"))
    with respx.mock() as mock:
        mock.get(url__regex=INFO_REFS).mock(return_value=advertisement_response("claude/a"))
        mock.get("https://gl.example/api/v4/projects/g%2Fp").respond(200, json={"id": 1})
        mock.get(
            url__regex=r"https://gl\.example/api/v4/projects/g%2Fp/merge_requests\?.*"
        ).respond(200, json=[])
        mock.get(url__regex=PULLS).respond(200, json=[pull_json()])
        assert await ctx.reconcile_all() is True
        assert not any("gl.example/api/v3" in str(c.request.url) for c in mock.calls)
        assert not any("github.com/api/v4" in str(c.request.url) for c in mock.calls)
    assert all(st.is_reconciled(n) for n in ("git", "api", "github"))
    await ctx.router.aclose()


# --- lookup cache ---------------------------------------------------------------------
class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_lookup_cache_ttl():
    cfg = make_cfg()
    clock = Clock()
    lookups = GithubLookups(UpstreamRouter(cfg, upstream_layout), cfg, clock=clock)
    with respx.mock() as mock:
        pull = mock.get(f"{UP}/pulls/7").mock(
            side_effect=[
                httpx.Response(200, json=pull_json()),
                httpx.Response(200, json=pull_json("main")),
            ]
        )
        kind = mock.get(f"{UP}/issues/7").respond(200, json={"pull_request": {}})
        assert await lookups.pull_in_namespace("github.com", "octo/repo", 7) is True
        clock.now += 29
        assert await lookups.pull_in_namespace("github.com:8080", "octo/repo", 7) is True
        assert await lookups.issue_kind("github.com", "octo/repo", 7) is IssueKind.PULL
        assert await lookups.issue_kind("GITHUB.com", "octo/repo", 7) is IssueKind.PULL
        assert pull.call_count == 1 and kind.call_count == 1
        clock.now += 2
        assert await lookups.pull_in_namespace("github.com", "octo/repo", 7) is False
        assert pull.call_count == 2


async def test_lookup_failures_are_not_cached():
    cfg = make_cfg()
    lookups = GithubLookups(UpstreamRouter(cfg, upstream_layout), cfg, clock=Clock())
    with respx.mock() as mock:
        run = mock.get(f"{UP}/actions/runs/9").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(
                    200,
                    json={
                        "head_branch": "claude/x",
                        "head_repository": {"id": 1},
                        "repository": {"id": 1},
                    },
                ),
            ]
        )
        assert await lookups.run_in_namespace("github.com", "octo/repo", 9) is None
        assert await lookups.run_in_namespace("github.com", "octo/repo", 9) is True
        assert run.call_count == 2


async def test_lookup_cache_is_keyed_per_host():
    cfg = make_cfg(endpoint(), endpoint("ghe.example"))
    lookups = GithubLookups(UpstreamRouter(cfg, upstream_layout), cfg, clock=Clock())
    with respx.mock() as mock:
        a = mock.get(f"{UP}/pulls/7").respond(200, json=pull_json())
        b = mock.get("https://ghe.example/api/v3/repos/octo/repo/pulls/7").respond(
            200, json=pull_json(head_id=2)
        )
        assert await lookups.pull_in_namespace("github.com", "octo/repo", 7) is True
        assert await lookups.pull_in_namespace("ghe.example", "octo/repo", 7) is False
        assert a.call_count == 1 and b.call_count == 1


async def test_lookup_on_unknown_host_is_unresolved():
    cfg = make_cfg()
    lookups = GithubLookups(UpstreamRouter(cfg, upstream_layout), cfg)
    with respx.mock() as mock:
        assert await lookups.pull_in_namespace("evil.example", "octo/repo", 7) is None
        assert await lookups.issue_kind("evil.example", "octo/repo", 7) is IssueKind.UNKNOWN
        assert await lookups.job_in_namespace("evil.example", "octo/repo", 1) is None
        assert not mock.calls


async def test_guard_cache_spans_requests_through_the_app():
    async with harness() as h:
        pull = h.mock.get(f"{UP}/pulls/7").respond(200, json=pull_json())
        h.forward_route()
        for _ in range(3):
            resp = await h.client.post(f"{REPO}/pulls/7/comments", json={"body": "b"})
            assert resp.status_code == 200
        assert pull.call_count == 1


# --- /policy report and config ---------------------------------------------------------
async def test_policy_report_lists_github_rows():
    cfg = make_cfg()
    ctx = build_context(cfg, State(":memory:"), AuditLog("-"))
    transport = httpx.ASGITransport(app=create_admin_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://admin") as c:
        body = (await c.get("/policy")).json()
    await ctx.router.aclose()
    report = body["hosts"]["github.com"]
    github_rows = {r["id"]: r for r in report["catalog"] if r["guard"] == "github"}
    assert set(github_rows) == {row.id for row in CATALOG}
    assert {r["guard"] for r in report["catalog"]} == {"transport", "github"}
    merge = github_rows["pull.merge"]["actions"][0]
    assert merge == {
        "id": "project.mr.merge",
        "criticality": "IRREVERSIBLE",
        "default": False,
        "active": False,
        "quota_kind": None,
    }
    create = github_rows["pull.create"]["actions"][0]
    assert create["active"] and create["default"] and create["quota_kind"] == "mr"
    issue = {a["id"]: a for a in github_rows["issue.update"]["actions"]}
    assert not issue["project.issue.close"]["active"] and issue["project.mr.close"]["active"]
    assert {"project.mr.merge", "repo.tag.create", "repo.branch.delete", "repo.tag.delete"} <= set(
        report["denials"]
    )


def test_effective_actions_type_cut_for_github():
    default_ids = {a.id for a in git_actions.DEFAULT}
    cfg = make_cfg()
    assert set(cfg.effective_actions("github.com")) == default_ids
    plain = Config(git_endpoints=(GitEndpoint(host="p.example", type="plain"),))
    assert "project.mr.create" not in plain.effective_actions("p.example")
    domain = Config(
        git_actions=("project.issue.comment", "repo.read"),
        git_endpoints=(GitEndpoint(host="github.com", type="github"),),
    )
    assert set(domain.effective_actions("github.com")) == {"project.issue.comment", "repo.read"}
    assert make_cfg(actions=("repo.read",)).effective_actions("github.com") == ("repo.read",)


def test_guard_identity_and_supported_actions():
    g = _guard(make_cfg())
    assert g.name == "github" and g.recognizers is CATALOG
    assert g.supported_actions == github_actions.SUPPORTED
    assert {a.id for a in github_actions.SUPPORTED} <= set(ALL_GITHUB_ACTIONS)


# --- audit ---------------------------------------------------------------------
async def test_audit_fields_for_allowed_write():
    async with harness() as h:
        h.mock.get(f"{UP}/pulls/7").respond(200, json=pull_json())
        h.forward_route()
        await h.client.patch(f"{REPO}/pulls/7", json={"state": "closed"})
        rec = h.last
        assert rec["guard"] == "github" and rec["decision"] == "allow"
        assert rec["actions"] == ("project.mr.close",) and rec["kind"] == "mr_update"
        assert rec["path"] == "/repos/octo/repo/pulls/7" and rec["project"] == "octo/repo"
        assert rec["upstream_status"] == 200 and "enabled_via" not in rec


async def test_audit_fields_for_denied_unrecognized_request():
    async with harness() as h:
        await h.client.post(f"{REPO}/merges", json={})
        rec = h.last
        assert rec["decision"] == "deny" and rec["kind"] is None
        assert "actions" not in rec and rec["upstream_status"] is None


async def test_audit_enabled_via_for_non_default_action():
    async with harness(make_cfg(actions=ALL_GITHUB_ACTIONS)) as h:
        h.forward_route()
        await h.client.post(f"{REPO}/issues", json={"title": "t"})
        assert h.last["enabled_via"] == ("project.issue.create",)
        assert h.last["kind"] == "issue_create"


async def test_audit_merge_denial_names_the_irreversible_action():
    async with harness() as h:
        await h.client.put(f"{REPO}/pulls/7/merge")
        rec = h.last
        assert rec["actions"] == ("project.mr.merge",)
        assert rec["enabled_via"] == ("project.mr.merge",)
        assert "irreversible" in rec["reason"]


async def test_written_write_record_uses_number_or_project():
    async with harness() as h:
        h.mock.get(f"{UP}/actions/runs/9").respond(
            200,
            json={"head_branch": "claude/x", "head_repository": {"id": 1}, "repository": {"id": 1}},
        )
        h.forward_route()
        await h.client.post(f"{REPO}/actions/runs/9/cancel")
        await h.client.post(f"{REPO}/pulls", json={"head": "claude/x"})
        rows = h.state.store.execute(
            "SELECT guard, host, kind, ref_or_iid FROM writes ORDER BY rowid"
        ).fetchall()
        assert [tuple(r) for r in rows] == [
            ("github", "github.com", "ci_trigger", "9"),
            ("github", "github.com", "mr", "octo/repo"),
        ]


async def test_self_referencing_link_fails_instead_of_looping(monkeypatch):
    from warden.guards.git.github import reconcile as gh_reconcile

    monkeypatch.setattr(gh_reconcile, "_MAX_PAGES", 3)
    g = _guard(make_cfg())
    with respx.mock() as mock:
        route = mock.get(url__regex=r"https://api\.github\.com/repos/octo/repo/pulls.*").respond(
            200, json=[], headers=_next(f"{API}/repos/octo/repo/pulls?page=2")
        )
        assert await g.reconcile() is False
    assert route.call_count == 3
