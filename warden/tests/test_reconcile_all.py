"""Per-guard reconcile isolation: each guard's fail-safe lock is its own — a
guard whose upstream is permanently unreachable stays locked and denies, while
every other guard keeps serving off its own fresh counts.
"""

from __future__ import annotations

import httpx

from warden.context import build_context
from warden.core.audit import AuditLog
from warden.core.config import Config
from warden.core.state import State


def _fresh_ctx(cfg):
    """A context on a brand-new (never-reconciled → both guards locked) state."""
    return build_context(cfg, State(":memory:"), AuditLog("-"))


def _git(ctx):
    return next(g for g in ctx.guards if g.name == "git")


def _api(ctx):
    return next(g for g in ctx.guards if g.name == "api")


def _mock_branches_ok(router):
    router.route(method="GET", url__regex=r".*/repository/branches.*").mock(
        return_value=httpx.Response(200, json=[{"name": "claude/a"}])
    )


def _mock_projects_ok(router):
    router.route(method="GET", url__regex=r".*/projects/[^/?]+$").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )


async def test_reconcile_all_unlocks_every_guard_on_full_success(cfg, respx_router):
    ctx = _fresh_ctx(cfg)
    assert _git(ctx).state_view("gitlab.example").locked is True
    assert _api(ctx).state_view("gitlab.example").locked is True

    _mock_branches_ok(respx_router)
    _mock_projects_ok(respx_router)
    respx_router.route(method="GET", url__regex=r".*/merge_requests\?.*").mock(
        return_value=httpx.Response(200, json=[])
    )

    ok = await ctx.reconcile_all()

    assert ok is True
    assert _git(ctx).state_view("gitlab.example").locked is False
    assert _api(ctx).state_view("gitlab.example").locked is False
    await ctx.router.aclose()


async def test_one_guards_permanent_failure_does_not_block_the_others(cfg, respx_router):
    # The isolation guarantee: the API guard's upstream is down but the git
    # guard's succeeds — only the API guard stays fail-safe-locked and denies.
    ctx = _fresh_ctx(cfg)

    _mock_branches_ok(respx_router)
    _mock_projects_ok(respx_router)
    respx_router.route(method="GET", url__regex=r".*/merge_requests\?.*").mock(
        return_value=httpx.Response(500)  # API upstream unreachable
    )

    ok = await ctx.reconcile_all()

    assert ok is False  # aggregate: not everything reconciled
    assert _git(ctx).state_view("gitlab.example").locked is False  # git serves — unaffected
    assert _api(ctx).state_view("gitlab.example").locked is True  # only the broken guard denies
    await ctx.router.aclose()


async def test_reconcile_all_unlocks_with_no_endpoints_configured(respx_router):
    # No endpoints configured makes no upstream call, so every guard unlocks
    # itself, and the warden opens the port and then denies ops.
    ctx = _fresh_ctx(Config())

    # No mock registered — any upstream call would raise respx.MockTransportError.
    ok = await ctx.reconcile_all()

    assert ok is True
    assert _git(ctx).state_view("gitlab.example").locked is False
    assert _api(ctx).state_view("gitlab.example").locked is False
    await ctx.router.aclose()


async def test_a_later_transient_failure_does_not_re_lock_a_reconciled_guard(cfg, respx_router):
    # Each per-guard lock is a one-way latch: a later transient failure does
    # NOT re-lock it — it keeps serving its last known-good MR count.
    ctx = _fresh_ctx(cfg)
    _mock_branches_ok(respx_router)
    _mock_projects_ok(respx_router)
    mrs = respx_router.route(method="GET", url__regex=r".*/merge_requests\?.*")

    mrs.mock(return_value=httpx.Response(200, json=[]))
    assert await ctx.reconcile_all() is True
    assert _api(ctx).state_view("gitlab.example").locked is False

    mrs.mock(return_value=httpx.Response(500))  # transient blip on a later cycle
    assert await ctx.reconcile_all() is False
    assert (
        _api(ctx).state_view("gitlab.example").locked is False
    )  # stays unlocked — latch not un-set
    await ctx.router.aclose()
