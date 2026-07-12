"""git guard reconcile: branch-listing pagination and fail-safe locking,
independent of the REST-API guard's own (MR) reconcile. The pagination test
guards against a busy project counting too low and wrongly allowing writes.
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
    # Page 1 advertises a next page via X-Next-Page; the branch that lives only
    # on page 2 must still be returned.
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
    # A guard's own reconcile rebuilds its counter and unlocks its OWN
    # per-guard view — independent of the REST-API guard.
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
    # Fail-safe: a failed reconcile must NOT unlock this guard's quota —
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
    """reconcile() makes no upstream call with no endpoints configured, and
    still unlocks its own view."""
    cfg_off = Config()
    state = State(":memory:")
    guard = _git_guard(cfg_off, state)
    assert guard.state_view(HOST).locked is True  # starts locked

    # No mock registered — any upstream call raises respx.MockTransportError.
    ok = await guard.reconcile()

    assert ok is True
    assert guard.state_view(HOST).locked is False  # unlocked so the warden can serve (and deny)
    await guard.router.aclose()


# --- per-endpoint reconcile skips closed endpoints ------------------------------


async def test_reconcile_branches_skips_a_closed_endpoint():
    """Must never attempt an upstream call for a closed endpoint (no usable
    read credential) — only the open endpoint's branches are listed/counted."""
    open_host, closed_host = "open.example", "closed.example"
    cfg = Config(
        git_endpoints=(
            GitEndpoint(host=open_host, type="gitlab", allowed_projects=("group/proj",)),
            GitEndpoint(host=closed_host, type="gitlab", allowed_projects=("group/proj",)),
        ),
        git_credentials={open_host: HostCredentials(read_token="r", write_token="w")},
    )
    assert cfg.access_mode(closed_host) == "closed"
    router = UpstreamRouter(cfg)
    branch_state = _git_guard(cfg, State(":memory:")).branch_state

    # No mock for closed.example — any call to it would raise
    # respx.MockTransportError, failing this test loudly.
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
    """A host with only repo.read must still reconcile like any other open
    endpoint — reconcile only does GETs and is never gated by the action gate."""
    cfg = Config(
        branch_prefixes=("claude/",),
        git_endpoints=(
            GitEndpoint(
                host=HOST,
                type="gitlab",
                allowed_projects=("group/proj",),
                actions=(REPO_READ.id,),
            ),
        ),
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
