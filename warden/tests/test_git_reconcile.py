"""git guard reconcile: branch listing from the ref advertisement and
fail-safe locking, independent of the REST-API guard's own (MR) reconcile.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from git_wire import INFO_REFS, advertisement, advertisement_response

from warden.core.audit import AuditLog
from warden.core.config import Config, GitEndpoint, HostCredentials
from warden.core.state import State
from warden.core.transport import UpstreamRouter
from warden.guards.git.actions import REPO_READ
from warden.guards.git.endpoints import upstream_layout
from warden.guards.git.transport.guard import GitGuard
from warden.guards.git.transport.reconcile import reconcile_branches

HOST = "gitlab.example"


def _git_guard(cfg, state) -> GitGuard:
    return GitGuard(cfg, state, AuditLog("-"), UpstreamRouter(cfg, upstream_layout))


async def test_reconcile_branches_counts_only_namespace_heads(cfg, state, respx_router):
    # Tags, non-namespace branches and other ref kinds never count.
    body = advertisement(
        "HEAD",
        "refs/heads/main",
        "refs/heads/claude/a",
        "refs/tags/claude/t",
        "refs/pull/1/head",
        "refs/heads/claude/z",
    )
    route = respx_router.route(method="GET", url__regex=INFO_REFS).mock(
        return_value=httpx.Response(200, content=body)
    )
    guard = _git_guard(cfg, state)

    ok = await reconcile_branches(cfg, guard.router, guard.branch_state)

    assert ok is True
    assert guard.branch_state.open_branches(HOST) == 2
    request = route.calls.last.request
    assert (
        str(request.url)
        == "https://gitlab.example/group/proj.git/info/refs?service=git-upload-pack"
    )
    assert request.headers["authorization"].startswith("Basic ")
    await guard.router.aclose()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>login</html>"),
        httpx.Response(200, content=b""),
        httpx.Response(200, content=b"0010# service=x\n0000"),
        httpx.Response(200, content=b"00ff# truncated"),
        httpx.Response(401, content=advertisement("refs/heads/claude/a")),
    ],
)
async def test_reconcile_fails_closed_on_a_non_advertisement(cfg, respx_router, response):
    guard = _git_guard(cfg, State(":memory:"))
    respx_router.route(method="GET", url__regex=INFO_REFS).mock(return_value=response)

    assert await guard.reconcile() is False
    assert guard.state_view(HOST).locked is True
    await guard.router.aclose()


async def test_reconcile_counts_branches_of_a_plain_endpoint():
    host = "plain.example"
    cfg = Config(
        git_endpoints=(GitEndpoint(host=host, type="plain", allowed_projects=("repo",)),),
        git_credentials={host: HostCredentials(read_token="r", write_token="w")},
    )
    guard = _git_guard(cfg, State(":memory:"))
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://plain.example/repo.git/info/refs?service=git-upload-pack").mock(
            return_value=advertisement_response("claude/a", "main")
        )
        assert await guard.reconcile() is True
    assert guard.branch_state.open_branches(host) == 1
    await guard.router.aclose()


async def test_reconcile_populates_branch_counter_and_unlocks_own_view(cfg, respx_router):
    # A guard's own reconcile rebuilds its counter and unlocks its OWN
    # per-guard view — independent of the REST-API guard.
    state = State(":memory:")
    guard = _git_guard(cfg, state)
    assert guard.state_view(HOST).locked is True  # locked until this guard's first success

    respx_router.route(method="GET", url__regex=INFO_REFS).mock(
        return_value=advertisement_response("claude/a", "claude/b")
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
    respx_router.route(method="GET", url__regex=INFO_REFS).mock(return_value=httpx.Response(500))

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
    router = UpstreamRouter(cfg, upstream_layout)
    branch_state = _git_guard(cfg, State(":memory:")).branch_state

    # No mock for closed.example — any call to it would raise
    # respx.MockTransportError, failing this test loudly.
    with respx.mock(assert_all_called=False) as router_mock:
        router_mock.route(
            method="GET", url__regex=r"https://open\.example/.*\.git/info/refs.*"
        ).mock(return_value=advertisement_response("claude/a"))

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
    respx_router.route(method="GET", url__regex=INFO_REFS).mock(
        return_value=advertisement_response("claude/a")
    )
    guard = _git_guard(cfg, State(":memory:"))

    ok = await reconcile_branches(cfg, guard.router, guard.branch_state)

    assert ok is True
    assert guard.branch_state.open_branches(HOST) == 1
    await guard.router.aclose()
