"""git guard reconcile (W6.2, W8.2, §6.11, §07 Punkt 6 step 4): branch-listing
pagination and fail-safe locking, now independent of the GitLab REST-API
guard's own (MR) reconcile — see ``test_forge.py`` for the MR side.

The pagination test is the regression guard for the quota-undercount bug:
listing stopped at the first 100 results, so a busy project counted too low
and the policy could wrongly ``allow`` further writes.
"""

from __future__ import annotations

import httpx
import respx

from warden.core.audit import AuditLog
from warden.core.config import Config, GitEndpoint, HostCredentials
from warden.core.state import State
from warden.core.transport import UpstreamRouter
from warden.guards.git.actions import REPO_READ
from warden.guards.git.transport.guard import GitGuard
from warden.guards.git.transport.reconcile import reconcile_branches

HOST = "gitlab.example"


def _git_guard(cfg, state) -> GitGuard:
    return GitGuard(cfg, state, AuditLog("-"), UpstreamRouter(cfg))


async def test_reconcile_branches_follows_every_page(cfg, state, respx_router):
    # Page 1 advertises a next page via X-Next-Page; the branch that lives ONLY
    # on page 2 must still be returned. A revert to a single per_page=100 request
    # would drop it and fail here.
    page1 = httpx.Response(
        200,
        json=[{"name": "claude/a"}, {"name": "main"}],
        headers={"X-Next-Page": "2"},
    )
    page2 = httpx.Response(200, json=[{"name": "claude/z"}])  # no next-page header
    route = respx_router.route(method="GET", url__regex=r".*/repository/branches.*").mock(
        side_effect=[page1, page2]
    )
    guard = _git_guard(cfg, state)

    ok = await reconcile_branches(cfg, guard.router, guard.branch_state)

    assert ok is True
    assert (
        guard.branch_state.open_branches(HOST) == 2
    )  # both pages, prefix-filtered ("main" dropped)
    assert "page=1" in str(route.calls[0].request.url)
    assert "page=2" in str(route.calls[1].request.url)
    await guard.router.aclose()


async def test_reconcile_populates_branch_counter_and_unlocks_own_view(cfg, respx_router):
    # A guard's own reconcile rebuilds its counter and unlocks its OWN per-guard
    # view — independent of the REST-API guard (see test_reconcile_all.py for the
    # cross-guard isolation the per-guard lock guarantees).
    state = State(":memory:")
    guard = _git_guard(cfg, state)
    assert guard.state_view(HOST).locked is True  # locked until this guard's first success

    respx_router.route(method="GET", url__regex=r".*/repository/branches.*").mock(
        return_value=httpx.Response(200, json=[{"name": "claude/a"}, {"name": "claude/b"}])
    )

    ok = await guard.reconcile()

    assert ok is True
    view = guard.state_view(HOST)
    assert view.locked is False
    assert view.open_branches == 2
    await guard.router.aclose()


async def test_reconcile_failure_keeps_own_view_locked(cfg, respx_router):
    # Fail-safe (§6.11): a failed reconcile must NOT unlock this guard's quota —
    # "empty = all free" is exactly the failure we refuse.
    state = State(":memory:")
    guard = _git_guard(cfg, state)
    respx_router.route(method="GET", url__regex=r".*/repository/branches.*").mock(
        return_value=httpx.Response(500)
    )

    ok = await guard.reconcile()

    assert ok is False
    assert guard.state_view(HOST).locked is True
    await guard.router.aclose()


async def test_reconcile_no_upstream_call_with_no_endpoints_configured(respx_router):
    """reconcile() must make NO upstream call when no [[git.endpoint]] is configured
    (the former GITLAB_MODE=off) — the shared host×project loop is simply a no-op
    over an empty ``effective_hosts`` — and it must still unlock its own view."""
    cfg_off = Config()
    state = State(":memory:")
    guard = _git_guard(cfg_off, state)
    assert guard.state_view(HOST).locked is True  # starts locked

    # No mock registered — any upstream call raises respx.MockTransportError.
    ok = await guard.reconcile()

    assert ok is True
    assert guard.state_view(HOST).locked is False  # unlocked so the warden can serve (and deny)
    await guard.router.aclose()


# --- per-endpoint reconcile skips closed endpoints (step 04) -------------------


async def test_reconcile_branches_skips_a_closed_endpoint():
    """reconcile_branches iterates cfg.git_endpoints directly and must never
    even attempt an upstream call for a closed one (no usable read
    credential) — only the open endpoint's branches are listed/counted."""
    open_host, closed_host = "open.example", "closed.example"
    cfg = Config(
        allowed_projects=("group/proj",),
        git_endpoints=(
            GitEndpoint(host=open_host, type="gitlab"),
            GitEndpoint(host=closed_host, type="gitlab"),
        ),
        git_credentials={open_host: HostCredentials(read_token="r", write_token="w")},
    )
    assert cfg.access_mode(closed_host) == "closed"
    router = UpstreamRouter(cfg)
    branch_state = _git_guard(cfg, State(":memory:")).branch_state

    # Two distinct hosts, neither the shared `respx_router` fixture's pinned
    # base_url — a bare respx mock, exactly like test_host_routing.py's own
    # multi-host tests. No mock for closed.example — any call to it would
    # raise respx.MockTransportError, failing this test loudly if reconcile
    # ever attempted to reach it.
    with respx.mock(assert_all_called=False) as router_mock:
        router_mock.route(
            method="GET", url__regex=r"https://open\.example/api/v4/.*/branches.*"
        ).mock(return_value=httpx.Response(200, json=[{"name": "claude/a"}]))

        ok = await reconcile_branches(cfg, router, branch_state)

    assert ok is True
    assert branch_state.open_branches(open_host) == 1
    assert branch_state.open_branches(closed_host) == 0
    await router.aclose()


# --- reconcile is independent of `actions` --------------------------------------


async def test_reconcile_ignores_a_host_with_no_push_action(respx_router):
    """A host whose effective actions are just ``repo.read`` (no
    ``repo.branch.push``) must still reconcile exactly like any other open
    endpoint: reconcile only ever does GETs and is never gated by the action
    gate — it doesn't even consult ``effective_actions``. A per-restart
    withdrawn ``repo.branch.push`` leaves existing branches untouched, only
    unable to grow."""
    cfg = Config(
        branch_prefixes=("claude/",),
        allowed_projects=("group/proj",),
        git_endpoints=(GitEndpoint(host=HOST, type="gitlab", actions=(REPO_READ.id,)),),
        git_credentials={HOST: HostCredentials(read_token="r", write_token="w")},
    )
    respx_router.route(method="GET", url__regex=r".*/repository/branches.*").mock(
        return_value=httpx.Response(200, json=[{"name": "claude/a"}])
    )
    guard = _git_guard(cfg, State(":memory:"))

    ok = await reconcile_branches(cfg, guard.router, guard.branch_state)

    assert ok is True
    assert guard.branch_state.open_branches(HOST) == 1
    await guard.router.aclose()
