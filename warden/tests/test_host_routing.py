"""Host→Upstream resolution and the host_gate kernel wiring.

Per-host state-keying tests live in test_git_state.py/test_api_state.py.
Config-schema-level parsing tests live in test_config.py."""

from __future__ import annotations

from dataclasses import replace

import httpx
import respx

from warden.app import create_app
from warden.context import build_context
from warden.core.audit import AuditLog
from warden.core.config import Config, GitEndpoint, HostCredentials
from warden.core.guard import host_gate
from warden.core.model import Decision
from warden.core.state import State
from warden.core.transport import UpstreamRouter, base_urls
from warden.guards.git.transport.guard import GitGuard

PROJ = "group%2Fproj"


def _multi_cfg(cfg: Config) -> Config:
    """The shared single-host cfg fixture (conftest.py), widened to two
    open endpoints: the fixture's own host, plus a second host with its own
    credentials — exactly the shape config_load produces from two
    [[git.endpoint]] entries."""
    return replace(
        cfg,
        git_endpoints=(
            GitEndpoint(host="gitlab.example", type="gitlab", allowed_projects=("group/proj",)),
            GitEndpoint(host="my-gitlab.de", type="gitlab", allowed_projects=("group/proj",)),
        ),
        git_credentials={
            "gitlab.example": HostCredentials(read_token="READ-TOKEN", write_token="WRITE-TOKEN"),
            "my-gitlab.de": HostCredentials(read_token="R2", write_token="W2"),
        },
    )


# --- base_urls (core/transport.py) ----------------------------------------------


def test_base_urls_gitlab_type_has_git_and_api_base():
    git_base, api_base = base_urls(GitEndpoint(host="gitlab.example", type="gitlab"))
    assert git_base == "https://gitlab.example"
    assert api_base == "https://gitlab.example/api/v4"


def test_base_urls_plain_type_has_no_api_base():
    git_base, api_base = base_urls(GitEndpoint(host="my-git-server.example", type="plain"))
    assert git_base == "https://my-git-server.example"
    assert api_base is None


def test_base_urls_unsupported_type_raises():
    import pytest

    with pytest.raises(ValueError):
        base_urls(GitEndpoint(host="x.example", type="bogus"))


# --- UpstreamRouter (core/transport.py) -----------------------------------------


def test_router_resolves_each_configured_host_to_its_own_upstream(cfg):
    router = UpstreamRouter(_multi_cfg(cfg))
    primary = router.resolve("gitlab.example")
    other = router.resolve("MY-GITLAB.DE:443")  # case- and port-insensitive
    assert primary is not None and other is not None
    assert primary is not other
    assert primary.rest_url("x").startswith("https://gitlab.example/api/v4")
    assert other.rest_url("x").startswith("https://my-gitlab.de/api/v4")


def test_router_denies_unknown_host(cfg):
    router = UpstreamRouter(_multi_cfg(cfg))
    assert router.resolve("evil.example") is None


def test_router_denies_a_closed_endpoint_host():
    """An endpoint with no usable read credential never gets a routable
    Upstream at all — `resolve` returns None exactly like an unknown host."""
    cfg = Config(git_endpoints=(GitEndpoint(host="gitlab.example", type="gitlab"),))
    assert cfg.access_mode("gitlab.example") == "closed"
    router = UpstreamRouter(cfg)
    assert router.resolve("gitlab.example") is None


def test_router_empty_endpoint_list_resolves_nothing(cfg):
    empty = replace(cfg, git_endpoints=(), git_credentials={})
    router = UpstreamRouter(empty)
    assert router.resolve("gitlab.example") is None
    assert router.resolve("anything.example") is None
    assert router.resolve("") is None


def test_router_for_host_matches_effective_hosts(cfg):
    multi = _multi_cfg(cfg)
    router = UpstreamRouter(multi)
    for host in multi.effective_hosts:
        assert router.for_host(host) is not None


# --- host_gate (core/guard.py) --------------------------------------------------


def test_host_gate_denies_everything_when_no_endpoint_configured(cfg):
    """Real default-deny: an empty endpoint list is not "feature off" — every
    host, including one that would otherwise look fine, is denied."""
    empty = replace(cfg, git_endpoints=(), git_credentials={})
    decision = host_gate("literally.anything", empty)
    assert decision == Decision(
        False, "host 'literally.anything' not in the multi-target allowlist"
    )


def test_host_gate_denies_unknown_host_not_in_allowlist(cfg):
    multi = _multi_cfg(cfg)
    decision = host_gate("evil.example", multi)
    assert decision == Decision(False, "host 'evil.example' not in the multi-target allowlist")


def test_host_gate_denies_a_closed_but_configured_host():
    """A host with a `[[git.endpoint]]` entry but no usable read token is
    denied here too — never reaches `UpstreamRouter.resolve` returning None
    past an "already denied" assertion downstream."""
    cfg = Config(git_endpoints=(GitEndpoint(host="gitlab.example", type="gitlab"),))
    decision = host_gate("gitlab.example", cfg)
    assert decision is not None and "not in the multi-target allowlist" in decision.reason


def test_host_gate_allows_a_listed_open_host(cfg):
    multi = _multi_cfg(cfg)
    assert host_gate("my-gitlab.de", multi) is None
    assert host_gate("gitlab.example", multi) is None


# --- end-to-end: a real request routes by Host header ---------------------------


async def test_end_to_end_request_routes_by_host_header_and_denies_unknown_host(cfg):
    multi = _multi_cfg(cfg)
    state = State(":memory:")
    state.mark_reconciled("git")
    state.mark_reconciled("api")
    ctx = build_context(multi, state, AuditLog("-"))
    app = create_app(ctx)
    transport = httpx.ASGITransport(app=app)

    with respx.mock(assert_all_called=False) as router:
        router.route(method="GET", url__regex=r"https://gitlab\.example/api/v4/.*").mock(
            return_value=httpx.Response(200, json=[{"name": "from-primary"}])
        )
        router.route(method="GET", url__regex=r"https://my-gitlab\.de/api/v4/.*").mock(
            return_value=httpx.Response(200, json=[{"name": "from-secondary"}])
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://warden") as c:
            resp1 = await c.get(
                f"/api/v4/projects/{PROJ}/repository/tree", headers={"Host": "gitlab.example"}
            )
            resp2 = await c.get(
                f"/api/v4/projects/{PROJ}/repository/tree", headers={"Host": "my-gitlab.de"}
            )
            resp3 = await c.get(
                f"/api/v4/projects/{PROJ}/repository/tree", headers={"Host": "evil.example"}
            )

    assert resp1.status_code == 200 and resp1.json()[0]["name"] == "from-primary"
    assert resp2.status_code == 200 and resp2.json()[0]["name"] == "from-secondary"
    assert (
        resp3.status_code == 403 and "not in the multi-target allowlist" in resp3.json()["reason"]
    )
    await ctx.aclose()


# --- reconcile runs per host, keyed separately (design spike section 4) --------


async def test_reconcile_branches_runs_per_host_with_same_project_path():
    # gitlab.com and my-gitlab.de both list "acme/infra" — this guards against a
    # single reconcile run silently combining or overwriting their branch counts.
    base = Config(state_db_path=":memory:")
    multi = replace(
        base,
        git_endpoints=(
            GitEndpoint(host="gitlab.com", type="gitlab", allowed_projects=("acme/infra",)),
            GitEndpoint(host="my-gitlab.de", type="gitlab", allowed_projects=("acme/infra",)),
        ),
        git_credentials={
            "gitlab.com": HostCredentials(read_token="r", write_token="w"),
            "my-gitlab.de": HostCredentials(read_token="r2", write_token="w2"),
        },
    )
    state = State(":memory:")
    guard = GitGuard(multi, state, AuditLog("-"), UpstreamRouter(multi))

    with respx.mock(assert_all_called=False) as router:
        router.route(
            method="GET", url__regex=r"https://gitlab\.com/api/v4/projects/.*branches.*"
        ).mock(return_value=httpx.Response(200, json=[{"name": "claude/a"}]))
        router.route(
            method="GET", url__regex=r"https://my-gitlab\.de/api/v4/projects/.*branches.*"
        ).mock(return_value=httpx.Response(200, json=[{"name": "claude/b"}, {"name": "claude/c"}]))
        ok = await guard.reconcile()

    assert ok is True
    assert guard.branch_state.open_branches("gitlab.com") == 1
    assert guard.branch_state.open_branches("my-gitlab.de") == 2
    await guard.router.aclose()


# --- regression: a closed endpoint must never crash/lock the whole reconcile ---
# A closed host's KeyError must never prevent mark_reconciled for the rest. ---


async def test_reconcile_completes_and_unlocks_when_one_of_two_endpoints_is_closed():
    """End-to-end reproduction of the reported bug: before the fix,
    `GitGuard.reconcile()` raised `KeyError` on the closed host, so it never
    returned and `mark_reconciled` was never called — the *whole* guard
    (including the open host) stayed fail-safe-locked forever."""
    cfg = Config(
        state_db_path=":memory:",
        git_endpoints=(
            GitEndpoint(host="open.example", type="gitlab", allowed_projects=("group/proj",)),
            GitEndpoint(host="closed.example", type="gitlab", allowed_projects=("group/proj",)),
        ),
        git_credentials={"open.example": HostCredentials(read_token="r", write_token="w")},
    )
    state = State(":memory:")
    guard = GitGuard(cfg, state, AuditLog("-"), UpstreamRouter(cfg))

    with respx.mock(assert_all_called=False) as router:
        router.route(
            method="GET", url__regex=r"https://open\.example/api/v4/projects/.*branches.*"
        ).mock(return_value=httpx.Response(200, json=[{"name": "claude/a"}]))
        ok = await guard.reconcile()

    assert ok is True
    assert state.is_reconciled(guard.name)  # the guard actually unlocked
    assert guard.branch_state.open_branches("open.example") == 1  # only the open host counted
    await guard.router.aclose()
