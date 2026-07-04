"""AppContext.reconcile_all (§07 Punkt 6 follow-up): the shared core lock is an
aggregate property — it unlocks only when EVERY guard reconciled successfully,
never on a single guard's success.

Regression guard for the fail-safe hole this closed: the git branch reconcile
and the REST-API MR reconcile mark the *same* shared lock, so if each guard
unlocked on its own, a git-only success would open the view while the API
guard's MR counter was left stale by a failed reconcile — letting it serve and
quota against an empty view.
"""

from __future__ import annotations

import httpx

from warden.context import build_context
from warden.core.audit import AuditLog
from warden.core.config import Config
from warden.core.state import State


def _fresh_ctx(cfg):
    """A context on a brand-new (never-reconciled → locked) state."""
    return build_context(cfg, State(":memory:"), AuditLog("-"))


async def test_reconcile_all_unlocks_only_when_all_guards_succeed(cfg, respx_router):
    ctx = _fresh_ctx(cfg)
    assert ctx.state.view().locked is True  # locked until the first full success

    respx_router.route(method="GET", url__regex=r".*/repository/branches.*").mock(
        return_value=httpx.Response(200, json=[{"name": "claude/a"}])
    )
    respx_router.route(method="GET", url__regex=r".*/projects/[^/?]+$").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    respx_router.route(method="GET", url__regex=r".*/merge_requests\?.*").mock(
        return_value=httpx.Response(
            200, json=[{"iid": 5, "state": "opened", "source_branch": "claude/x"}]
        )
    )

    ok = await ctx.reconcile_all()

    assert ok is True
    assert ctx.state.view().locked is False
    await ctx.router.aclose()


async def test_reconcile_all_stays_locked_when_one_guard_fails(cfg, respx_router):
    # THE fix: the git branch reconcile succeeds but the REST-API MR reconcile
    # fails — the shared lock must stay engaged. Before, git's success unlocked
    # the view, letting the API guard serve/quota against a stale (empty) MR
    # counter.
    ctx = _fresh_ctx(cfg)

    respx_router.route(method="GET", url__regex=r".*/repository/branches.*").mock(
        return_value=httpx.Response(200, json=[{"name": "claude/a"}])
    )
    respx_router.route(method="GET", url__regex=r".*/projects/[^/?]+$").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    respx_router.route(method="GET", url__regex=r".*/merge_requests\?.*").mock(
        return_value=httpx.Response(500)  # API reconcile fails
    )

    ok = await ctx.reconcile_all()

    assert ok is False
    assert ctx.state.view().locked is True  # still locked despite the git guard's success
    await ctx.router.aclose()


async def test_reconcile_all_unlocks_in_off_mode(respx_router):
    # off mode makes no upstream call; every guard reports success, so the
    # orchestrator unlocks (the warden opens the port and then denies ops).
    ctx = _fresh_ctx(Config(gitlab_mode="off"))

    # No mock registered — any upstream call would raise respx.MockTransportError.
    ok = await ctx.reconcile_all()

    assert ok is True
    assert ctx.state.view().locked is False
    await ctx.router.aclose()


async def test_reconcile_all_stays_unlocked_after_a_later_transient_failure(cfg, respx_router):
    # Once the latch is set by a full success, a later per-guard failure does NOT
    # re-lock — the lock means "has fully reconciled at least once", so the last
    # known-good counters keep serving instead of flapping locked on a blip.
    ctx = _fresh_ctx(cfg)
    branches = respx_router.route(method="GET", url__regex=r".*/repository/branches.*")
    projects = respx_router.route(method="GET", url__regex=r".*/projects/[^/?]+$")
    mrs = respx_router.route(method="GET", url__regex=r".*/merge_requests\?.*")

    branches.mock(return_value=httpx.Response(200, json=[{"name": "claude/a"}]))
    projects.mock(return_value=httpx.Response(200, json={"id": 1}))
    mrs.mock(return_value=httpx.Response(200, json=[]))
    assert await ctx.reconcile_all() is True
    assert ctx.state.view().locked is False

    mrs.mock(return_value=httpx.Response(500))  # transient blip on a later cycle
    assert await ctx.reconcile_all() is False
    assert ctx.state.view().locked is False  # stays unlocked — latch is not un-set
    await ctx.router.aclose()
