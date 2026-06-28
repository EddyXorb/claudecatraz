"""context.py (W6.2, W8.2, §6.11): reconcile pagination, fail-safe locking,
MR-ownership rules and the ownership/service-account caches.

The pagination tests are the regression guard for the quota-undercount bug:
listing stopped at the first 100 results, so a busy project counted too low and
the policy could wrongly ``allow`` further writes.
"""

from __future__ import annotations

import httpx

from warden.audit import AuditLog
from warden.config import Config
from warden.context import AppContext
from warden.state import State
from warden.upstream import Upstream


def _ctx(cfg, *, sa=42, clock=None) -> AppContext:
    st = State(":memory:")
    kwargs = {"clock": clock} if clock else {}
    ctx = AppContext(cfg, Upstream(cfg), st, AuditLog("-"), **kwargs)
    ctx.service_account_id = sa
    return ctx


# --- pagination (the bugfix) ---------------------------------------------------
async def test_list_branches_follows_every_page(ctx, respx_router):
    # Page 1 advertises a next page via X-Next-Page; the branch that lives ONLY
    # on page 2 must still be returned. A revert to a single per_page=100 request
    # would drop it and fail here.
    page1 = httpx.Response(
        200,
        json=[{"name": "claude/a"}, {"name": "main"}],
        headers={"X-Next-Page": "2"},
    )
    page2 = httpx.Response(200, json=[{"name": "claude/z"}])  # no next-page header
    route = respx_router.route(
        method="GET", url__regex=r".*/repository/branches.*"
    ).mock(side_effect=[page1, page2])

    branches = await ctx._list_claude_branches("group%2Fproj")

    assert branches == ["claude/a", "claude/z"]  # both pages, prefix-filtered
    assert "page=1" in str(route.calls[0].request.url)
    assert "page=2" in str(route.calls[1].request.url)
    await ctx.upstream.aclose()


async def test_list_mrs_paginates_filters_and_scopes_to_author(ctx, respx_router):
    page1 = httpx.Response(
        200,
        json=[{"iid": 1, "state": "opened", "source_branch": "claude/x"}],
        headers={"X-Next-Page": "2"},
    )
    page2 = httpx.Response(
        200,
        json=[
            {"iid": 2, "state": "opened", "source_branch": "feature/y"},  # no prefix
            {"iid": 3, "state": "opened", "source_branch": "claude/z"},
        ],
    )
    route = respx_router.route(
        method="GET", url__regex=r".*/merge_requests\?.*"
    ).mock(side_effect=[page1, page2])

    mrs = await ctx._list_claude_mrs("group%2Fproj", 42)

    assert mrs == [(1, "opened"), (3, "opened")]  # both pages, prefix-filtered
    assert "author_id=42" in str(route.calls[0].request.url)  # only the SA's MRs
    await ctx.upstream.aclose()


# --- reconcile (W8.2 / §6.11) --------------------------------------------------
async def test_reconcile_populates_counters_and_unlocks(cfg, respx_router):
    ctx = _ctx(cfg)
    assert ctx.state.view().locked is True  # locked until first successful reconcile

    respx_router.route(method="GET", url__regex=r".*/projects/[^/?]+$").mock(
        return_value=httpx.Response(200, json={"id": 12345})
    )
    respx_router.route(method="GET", url__regex=r".*/repository/branches.*").mock(
        return_value=httpx.Response(200, json=[{"name": "claude/a"}, {"name": "claude/b"}])
    )
    respx_router.route(method="GET", url__regex=r".*/merge_requests\?.*").mock(
        return_value=httpx.Response(
            200, json=[{"iid": 5, "state": "opened", "source_branch": "claude/x"}]
        )
    )

    ok = await ctx.reconcile()

    assert ok is True
    view = ctx.state.view()
    assert view.locked is False
    assert view.open_branches == 2
    assert view.open_mrs == 1
    # The numeric-id alias was resolved and added to the allowlist (R6 by id form).
    assert ctx.cfg.allowed_project_ids == ("12345",)
    assert ctx.cfg.project_allowed("12345")
    await ctx.upstream.aclose()


async def test_reconcile_failure_keeps_state_locked(cfg, respx_router):
    # Fail-safe (§6.11): a partial/failed reconcile must NOT unlock the quota —
    # "empty = all free" is exactly the failure we refuse.
    ctx = _ctx(cfg)
    respx_router.route(method="GET", url__regex=r".*/projects/[^/?]+$").mock(
        return_value=httpx.Response(200, json={"id": 12345})
    )
    respx_router.route(method="GET", url__regex=r".*/repository/branches.*").mock(
        return_value=httpx.Response(500)
    )

    ok = await ctx.reconcile()

    assert ok is False
    assert ctx.state.view().locked is True
    await ctx.upstream.aclose()


# --- MR ownership (W6.2) -------------------------------------------------------
async def test_ownership_true_when_prefixed_and_authored_by_sa(ctx, respx_router):
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(
            200, json={"source_branch": "claude/x", "author": {"id": 42}}
        )
    )
    assert await ctx.mr_owned_by_claude("group/proj", 7) is True
    await ctx.upstream.aclose()


async def test_ownership_false_when_author_differs(ctx, respx_router):
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(
            200, json={"source_branch": "claude/x", "author": {"id": 999}}
        )
    )
    assert await ctx.mr_owned_by_claude("group/proj", 7) is False
    await ctx.upstream.aclose()


async def test_ownership_false_when_prefix_missing(ctx, respx_router):
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(
            200, json={"source_branch": "feature/x", "author": {"id": 42}}
        )
    )
    assert await ctx.mr_owned_by_claude("group/proj", 7) is False
    await ctx.upstream.aclose()


async def test_ownership_none_when_lookup_fails(ctx, respx_router):
    # None ⇒ the policy denies (default-deny holds); it must not be coerced to False/True.
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(404)
    )
    assert await ctx.mr_owned_by_claude("group/proj", 7) is None
    await ctx.upstream.aclose()


async def test_ownership_cached_within_ttl_then_refetched(cfg, respx_router):
    now = {"t": 1000.0}
    ctx = _ctx(cfg, clock=lambda: now["t"])
    route = respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(
            200, json={"source_branch": "claude/x", "author": {"id": 42}}
        )
    )

    assert await ctx.mr_owned_by_claude("group/proj", 7) is True
    assert await ctx.mr_owned_by_claude("group/proj", 7) is True
    assert route.call_count == 1  # second call served from the 30s cache

    now["t"] += 31  # past the TTL
    assert await ctx.mr_owned_by_claude("group/proj", 7) is True
    assert route.call_count == 2  # cache expired → refetched
    await ctx.upstream.aclose()


# --- service account (W6.2) ----------------------------------------------------
async def test_resolve_service_account_is_cached(cfg, respx_router):
    ctx = _ctx(cfg, sa=None)
    route = respx_router.route(method="GET", url__regex=r".*/user$").mock(
        return_value=httpx.Response(200, json={"id": 77})
    )
    assert await ctx.resolve_service_account() == 77
    assert await ctx.resolve_service_account() == 77
    assert route.call_count == 1  # resolved once, then memoised
    await ctx.upstream.aclose()


async def test_resolve_service_account_none_on_error(cfg, respx_router):
    ctx = _ctx(cfg, sa=None)
    respx_router.route(method="GET", url__regex=r".*/user$").mock(
        return_value=httpx.Response(403)
    )
    assert await ctx.resolve_service_account() is None
    await ctx.upstream.aclose()


# --- GITLAB_MODE gates (mode-enforcement, step 6/7) ----------------------------

async def test_resolve_service_account_no_upstream_call_when_writes_disabled(respx_router):
    """resolve_service_account() must make NO upstream call in off or read-only mode."""
    for mode in ("off", "read-only"):
        cfg_mode = Config(
            allowed_projects=("group/proj",),
            read_token="r",
            gitlab_mode=mode,
        )
        ctx = _ctx(cfg_mode, sa=None)
        # No mock registered — any upstream call raises respx.MockTransportError.
        result = await ctx.resolve_service_account()
        assert result is None, f"expected None in {mode} mode, got {result}"
        await ctx.upstream.aclose()


async def test_reconcile_no_upstream_call_in_off_mode(respx_router):
    """reconcile() must make NO upstream call when GITLAB_MODE=off, and must unlock state."""
    cfg_off = Config(gitlab_mode="off")
    ctx = _ctx(cfg_off, sa=None)
    assert ctx.state.view().locked is True  # starts locked

    # No mock registered — any upstream call raises respx.MockTransportError.
    ok = await ctx.reconcile()

    assert ok is True
    assert ctx.state.view().locked is False  # unlocked so the warden can serve (and deny)
    await ctx.upstream.aclose()
