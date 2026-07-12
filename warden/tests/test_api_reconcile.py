"""reconcile.py: the REST-API guard's MR reconcile pagination, fail-safe
locking, and numeric-id project-alias resolution. The pagination test guards
against a busy project counting too low and wrongly allowing further writes.
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
    """A guard on its own fresh (never-reconciled) state, so the lock/reconcile
    tests below see the real starting condition."""
    return ApiGuard(cfg, State(":memory:"), AuditLog("-"), UpstreamRouter(cfg))


# --- project_allowed (resource allowlist) ---------------------------------------
def test_project_allowed_matches_reconciled_numeric_id_alias_only(api_guard, cfg):
    host = cfg.git_endpoints[0].host
    api_guard.project_id_aliases = {host: {"81882161"}}
    assert api_guard.project_allowed(host, "81882161")
    assert not api_guard.project_allowed(host, "99999999")  # unknown id: default-deny


def test_project_allowed_numeric_id_alias_is_scoped_per_host():
    """Host A's resolved numeric id must never authorise host B's project —
    the alias set is keyed per host, not one flat set shared by every host."""
    host_a, host_b = "gitlab.example", "other-gitlab.example"
    cfg = Config(
        git_endpoints=(
            GitEndpoint(host=host_a, type="gitlab", allowed_projects=("group/proj",)),
            GitEndpoint(host=host_b, type="gitlab"),
        ),
        git_credentials={
            host_a: HostCredentials(read_token="r", write_token="w"),
            host_b: HostCredentials(read_token="r", write_token="w"),
        },
    )
    guard = ApiGuard(cfg, State(":memory:"), AuditLog("-"), UpstreamRouter(cfg))
    guard.project_id_aliases = {host_a: {"111"}}
    assert guard.project_allowed(host_a, "111")
    assert not guard.project_allowed(host_b, "111")


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
            # foreign author, but namespace source_branch — still counted
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
    assert resolved_ids == {HOST: {"12345"}}
    assert guard.mr_state.open_mrs(HOST) == 2  # both pages, namespace-filtered only


# --- reconcile -------------------------------------------------------------
async def test_reconcile_populates_counters_and_unlocks_own_view(cfg, respx_router):
    # A guard's own reconcile rebuilds its MR counter/aliases and unlocks its
    # OWN per-guard view — independent of the git guard.
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
    # The numeric-id alias was resolved and added to the guard's alias set —
    # Config itself is never mutated.
    assert guard.project_id_aliases == {HOST: {"12345"}}
    assert guard.project_allowed(HOST, "12345")


async def test_reconcile_failure_keeps_own_view_locked(cfg, respx_router):
    # Fail-safe: a failed reconcile must NOT unlock this guard's quota —
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


# --- no endpoints configured ----------------------------------------------------


async def test_reconcile_no_upstream_call_with_no_endpoints_configured(respx_router):
    """reconcile() makes no upstream call with no endpoints configured, and
    still unlocks its own view."""
    cfg_off = Config()
    guard = _api_guard(cfg_off)
    assert guard.state_view(HOST).locked is True  # starts locked

    # No mock registered — any upstream call raises respx.MockTransportError.
    ok = await guard.reconcile()

    assert ok is True
    assert guard.state_view(HOST).locked is False  # unlocked so the warden can serve (and deny)


# --- per-endpoint reconcile skips closed endpoints ------------------------------


async def test_reconcile_mrs_skips_a_closed_endpoint():
    """Must never attempt an upstream call for a closed endpoint (no usable
    read credential) — only the open endpoint's MRs are listed/counted."""
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
    mr_state = _api_guard(cfg).mr_state

    # No mock for closed.example — any call to it would raise
    # respx.MockTransportError, failing this test loudly.
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
    assert resolved_ids == {open_host: {"1"}}
    assert mr_state.open_mrs(open_host) == 1
    assert mr_state.open_mrs(closed_host) == 0
    await router.aclose()
