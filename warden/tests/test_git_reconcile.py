"""git guard reconcile (W6.2, W8.2, §6.11, §07 Punkt 6 step 4): branch-listing
pagination and fail-safe locking, now independent of the GitLab REST-API
guard's own (MR) reconcile — see ``test_forge.py`` for the MR side.

The pagination test is the regression guard for the quota-undercount bug:
listing stopped at the first 100 results, so a busy project counted too low
and the policy could wrongly ``allow`` further writes.
"""

from __future__ import annotations

import httpx

from warden.core.audit import AuditLog
from warden.core.config import Config
from warden.core.state import State
from warden.core.transport import UpstreamRouter
from warden.guards.git.guard import GitGuard
from warden.guards.git.reconcile import reconcile_branches


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
    assert guard.branch_state.open_branches() == 2  # both pages, prefix-filtered ("main" dropped)
    assert "page=1" in str(route.calls[0].request.url)
    assert "page=2" in str(route.calls[1].request.url)
    await guard.router.aclose()


async def test_reconcile_populates_branch_counter_and_unlocks_own_view(cfg, respx_router):
    # A guard's own reconcile rebuilds its counter and unlocks its OWN per-guard
    # view — independent of the REST-API guard (see test_reconcile_all.py for the
    # cross-guard isolation the per-guard lock guarantees).
    state = State(":memory:")
    guard = _git_guard(cfg, state)
    assert guard.state_view().locked is True  # locked until this guard's first success

    respx_router.route(method="GET", url__regex=r".*/repository/branches.*").mock(
        return_value=httpx.Response(200, json=[{"name": "claude/a"}, {"name": "claude/b"}])
    )

    ok = await guard.reconcile()

    assert ok is True
    view = guard.state_view()
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
    assert guard.state_view().locked is True
    await guard.router.aclose()


async def test_reconcile_no_upstream_call_in_off_mode(respx_router):
    """reconcile() must make NO upstream call when GITLAB_MODE=off, and unlock its own view."""
    cfg_off = Config(gitlab_mode="off")
    state = State(":memory:")
    guard = _git_guard(cfg_off, state)
    assert guard.state_view().locked is True  # starts locked

    # No mock registered — any upstream call raises respx.MockTransportError.
    ok = await guard.reconcile()

    assert ok is True
    assert guard.state_view().locked is False  # unlocked so the warden can serve (and deny)
    await guard.router.aclose()
