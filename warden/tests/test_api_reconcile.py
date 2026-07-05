"""reconcile.py: the REST-API guard's MR reconcile pagination, fail-safe
locking, and numeric-id project-alias resolution (M6). See
``test_api_mr_namespace.py`` for the MR source-branch-namespace-lookup side
and ``test_git_reconcile.py`` for the git guard's own (branch) reconcile.

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
from warden.guards.git.gitlab.guard import ApiGuard
from warden.guards.git.gitlab.reconcile import reconcile_mrs

HOST = "gitlab.example"


def _api_guard(cfg) -> ApiGuard:
    """A guard on its own fresh (never-reconciled) state — unlike the shared
    ``api_guard`` fixture (built on the pre-reconciled ``state`` fixture), so
    the lock/reconcile tests below see the real starting condition."""
    return ApiGuard(cfg, State(":memory:"), AuditLog("-"), UpstreamRouter(cfg))


# --- project_allowed (M6) -------------------------------------------------------
def test_project_allowed_matches_reconciled_numeric_id_alias_only(api_guard):
    api_guard.project_id_aliases = {"81882161"}
    assert api_guard.project_allowed("81882161")
    assert not api_guard.project_allowed("99999999")  # unknown id: default-deny


# --- pagination (the bugfix) ---------------------------------------------------
async def test_reconcile_mrs_paginates_and_filters_by_namespace_author_independent(
    cfg, respx_router
):
    guard = _api_guard(cfg)
    page1 = httpx.Response(
        200,
        json=[{"iid": 1, "state": "opened", "source_branch": "claude/x"}],
        headers={"X-Next-Page": "2"},
    )
    page2 = httpx.Response(
        200,
        json=[
            {"iid": 2, "state": "opened", "source_branch": "feature/y"},  # no prefix
            # foreign author, but namespace source_branch — still counted (§07 Punkt 4)
            {
                "iid": 3,
                "state": "opened",
                "source_branch": "claude/z",
                "author": {"id": 999},
            },
        ],
    )
    respx_router.route(method="GET", url__regex=r".*/merge_requests\?.*").mock(
        side_effect=[page1, page2]
    )
    respx_router.route(method="GET", url__regex=r".*/projects/[^/?]+$").mock(
        return_value=httpx.Response(200, json={"id": 12345})
    )

    ok, resolved_ids = await reconcile_mrs(cfg, guard.router, guard.mr_state)

    assert ok is True
    assert resolved_ids == {"12345"}
    assert guard.mr_state.open_mrs(HOST) == 2  # both pages, namespace-filtered only


# --- reconcile (W8.2 / §6.11) --------------------------------------------------
async def test_reconcile_populates_counters_and_unlocks_own_view(cfg, respx_router):
    # A guard's own reconcile rebuilds its MR counter/aliases and unlocks its OWN
    # per-guard view — independent of the git guard (see test_reconcile_all.py for
    # the cross-guard isolation the per-guard lock guarantees).
    guard = _api_guard(cfg)
    assert guard.state_view(HOST).locked is True  # locked until this guard's first success

    respx_router.route(method="GET", url__regex=r".*/projects/[^/?]+$").mock(
        return_value=httpx.Response(200, json={"id": 12345})
    )
    respx_router.route(method="GET", url__regex=r".*/merge_requests\?.*").mock(
        return_value=httpx.Response(
            200, json=[{"iid": 5, "state": "opened", "source_branch": "claude/x"}]
        )
    )

    ok = await guard.reconcile()

    assert ok is True
    view = guard.state_view(HOST)
    assert view.locked is False
    assert view.open_mrs == 1
    # The numeric-id alias was resolved and added to the guard's alias set
    # (R6 by id form) — Config itself is never mutated (D2).
    assert guard.project_id_aliases == {"12345"}
    assert guard.project_allowed("12345")


async def test_reconcile_failure_keeps_own_view_locked(cfg, respx_router):
    # Fail-safe (§6.11): a failed reconcile must NOT unlock this guard's quota —
    # "empty = all free" is exactly the failure we refuse.
    guard = _api_guard(cfg)
    respx_router.route(method="GET", url__regex=r".*/projects/[^/?]+$").mock(
        return_value=httpx.Response(200, json={"id": 12345})
    )
    respx_router.route(method="GET", url__regex=r".*/merge_requests\?.*").mock(
        return_value=httpx.Response(500)
    )

    ok = await guard.reconcile()

    assert ok is False
    assert guard.state_view(HOST).locked is True


# --- no endpoints configured (the former GITLAB_MODE gates, step 6/7) ---------


async def test_reconcile_no_upstream_call_with_no_endpoints_configured(respx_router):
    """reconcile() must make NO upstream call when no [[git.endpoint]] is configured
    (the former GITLAB_MODE=off) — the shared host×project loop is simply a no-op
    over an empty ``effective_hosts`` — and it must still unlock its own view."""
    cfg_off = Config()
    guard = _api_guard(cfg_off)
    assert guard.state_view(HOST).locked is True  # starts locked

    # No mock registered — any upstream call raises respx.MockTransportError.
    ok = await guard.reconcile()

    assert ok is True
    assert guard.state_view(HOST).locked is False  # unlocked so the warden can serve (and deny)


# --- per-endpoint reconcile skips closed endpoints (step 04) -------------------


async def test_reconcile_mrs_skips_a_closed_endpoint():
    """reconcile_mrs iterates cfg.git_endpoints directly and must never even
    attempt an upstream call for a closed one (no usable read credential) —
    only the open endpoint's MRs are listed/counted."""
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
    mr_state = _api_guard(cfg).mr_state

    # Two distinct hosts, neither the shared `respx_router` fixture's pinned
    # base_url — a bare respx mock, exactly like test_host_routing.py's own
    # multi-host tests. No mock for closed.example — any call to it would
    # raise respx.MockTransportError, failing this test loudly if reconcile
    # ever attempted to reach it.
    with respx.mock(assert_all_called=False) as router_mock:
        router_mock.route(
            method="GET", url__regex=r"https://open\.example/api/v4/projects/[^/?]+$"
        ).mock(return_value=httpx.Response(200, json={"id": 1}))
        router_mock.route(
            method="GET", url__regex=r"https://open\.example/api/v4/.*/merge_requests\?.*"
        ).mock(
            return_value=httpx.Response(
                200, json=[{"iid": 1, "state": "opened", "source_branch": "claude/x"}]
            )
        )

        ok, resolved_ids = await reconcile_mrs(cfg, router, mr_state)

    assert ok is True
    assert resolved_ids == {"1"}
    assert mr_state.open_mrs(open_host) == 1
    assert mr_state.open_mrs(closed_host) == 0
    await router.aclose()
