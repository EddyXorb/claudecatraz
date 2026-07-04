"""Host→Upstream resolution + the ``host_gate`` kernel wiring (§07 Punkt 8
follow-up — the "Umsetzungsschnitt dieser PR" follow-up work named in
``docs/design/architecture-generalization/08-multi-target.md`` section 6:
Host→Upstream resolution (section 2) and per-host credentials (section 3)
wired into the request/kernel path).

Per-host state-keying tests live in ``test_git_state.py``/``test_api_state.py``
(the ``BranchState``/``MrState`` tables) and this file's
``test_reconcile_branches_runs_per_host_with_same_project_path`` (the
reconcile loop that populates them). Credential slug/collision parsing tests
live in ``test_config.py``.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import respx

from warden.app import create_app
from warden.context import build_context
from warden.core.audit import AuditLog
from warden.core.config import Config, HostCredentials
from warden.core.guard import host_gate
from warden.core.model import Decision
from warden.core.rules import R6
from warden.core.state import State
from warden.core.transport import UpstreamRouter
from warden.guards.git.guard import GitGuard

PROJ = "group%2Fproj"


def _multi_cfg(cfg: Config) -> Config:
    """The shared single-host ``cfg`` fixture (conftest.py), widened to two
    hosts: the fixture's own host aliased as primary, plus a second host with
    its own credentials — exactly the shape ``config_load`` produces from
    ``[git.urls] hosts = ["gitlab.example", "my-gitlab.de"]``."""
    return replace(
        cfg,
        host_order=("gitlab.example", "my-gitlab.de"),
        allowed_hosts=frozenset({"gitlab.example", "my-gitlab.de"}),
        host_credentials={
            "gitlab.example": HostCredentials(read_token="READ-TOKEN", write_token="WRITE-TOKEN"),
            "my-gitlab.de": HostCredentials(read_token="R2", write_token="W2"),
        },
    )


# --- UpstreamRouter (core/transport.py) -----------------------------------------


def test_single_target_router_ignores_host_header(cfg):
    router = UpstreamRouter(cfg)
    a = router.resolve("anything.example")
    b = router.resolve("")
    assert a is not None and b is not None
    assert a is b  # same single Upstream regardless of what the header says


def test_multi_target_router_resolves_known_host_to_its_own_upstream(cfg):
    router = UpstreamRouter(_multi_cfg(cfg))
    primary = router.resolve("gitlab.example")
    other = router.resolve("MY-GITLAB.DE:443")  # case- and port-insensitive
    assert primary is not None and other is not None
    assert primary is not other
    assert primary.rest_url("x").startswith("https://gitlab.example/api/v4")
    assert other.rest_url("x").startswith("https://my-gitlab.de/api/v4")


def test_multi_target_router_denies_unknown_host(cfg):
    router = UpstreamRouter(_multi_cfg(cfg))
    assert router.resolve("evil.example") is None


def test_multi_target_router_for_host_matches_effective_hosts(cfg):
    multi = _multi_cfg(cfg)
    router = UpstreamRouter(multi)
    for host in multi.effective_hosts:
        assert router.for_host(host) is not None


# --- host_gate (core/guard.py) --------------------------------------------------


def test_host_gate_allows_everything_when_allowlist_empty(cfg):
    assert host_gate("literally.anything", cfg) is None


def test_host_gate_denies_unknown_host_with_r6(cfg):
    multi = _multi_cfg(cfg)
    decision = host_gate("evil.example", multi)
    assert decision == Decision(False, R6, "host 'evil.example' not in the multi-target allowlist")


def test_host_gate_allows_a_listed_host(cfg):
    multi = _multi_cfg(cfg)
    assert host_gate("my-gitlab.de", multi) is None
    assert host_gate("gitlab.example", multi) is None


# --- end-to-end: a real request routes by Host header ---------------------------


async def test_end_to_end_request_routes_by_host_header_and_denies_unknown_host(cfg):
    multi = _multi_cfg(cfg)
    state = State(":memory:")
    state.mark_reconciled()
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
    assert resp3.status_code == 403 and resp3.json()["rule"] == "R6"
    await ctx.aclose()


# --- reconcile runs per host, keyed separately (design spike section 4) --------


async def test_reconcile_branches_runs_per_host_with_same_project_path():
    # gitlab.com and my-gitlab.de both happen to list "acme/infra" — the
    # regression this guards is a single reconcile run silently combining
    # (or overwriting) their branch counts.
    base = Config(
        allowed_projects=("acme/infra",),
        read_token="r",
        write_token="w",
        state_db_path=":memory:",
    )
    multi = replace(
        base,
        host_order=("gitlab.com", "my-gitlab.de"),
        allowed_hosts=frozenset({"gitlab.com", "my-gitlab.de"}),
        host_credentials={
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
    assert guard.branch_state.open_branches() == 3  # 1 (gitlab.com) + 2 (my-gitlab.de)
    await guard.router.aclose()
