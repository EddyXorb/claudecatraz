"""mr_namespace.py: the REST-API guard's MR source-branch-namespace rule + its
cache. See ``test_api_reconcile.py`` for the reconcile/pagination side.
"""

from __future__ import annotations

import httpx

from warden.core.transport import UpstreamRouter
from warden.guards.git.gitlab.mr_namespace import MrNamespace

HOST = "gitlab.example"  # matches the `cfg` fixture's `[[git.endpoint]]` host (conftest.py)


def _mr_namespace(cfg, *, clock=None) -> MrNamespace:
    kwargs = {"clock": clock} if clock else {}
    return MrNamespace(UpstreamRouter(cfg), cfg, **kwargs)


async def test_source_in_namespace_true_when_prefixed_regardless_of_author(cfg, respx_router):
    # A namespace source_branch is enough — the author (a foreign colleague here)
    # no longer matters (§07 Punkt 4: blast-radius is the branch namespace).
    mr_namespace = _mr_namespace(cfg)
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "claude/x", "author": {"id": 999}})
    )
    assert await mr_namespace.source_in_namespace(HOST, "group/proj", 7) is True


async def test_source_in_namespace_false_when_prefix_missing(cfg, respx_router):
    mr_namespace = _mr_namespace(cfg)
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "feature/x", "author": {"id": 42}})
    )
    assert await mr_namespace.source_in_namespace(HOST, "group/proj", 7) is False


async def test_source_in_namespace_none_when_lookup_fails(cfg, respx_router):
    # None ⇒ the policy denies (default-deny holds); it must not be coerced to False/True.
    mr_namespace = _mr_namespace(cfg)
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(404)
    )
    assert await mr_namespace.source_in_namespace(HOST, "group/proj", 7) is None


async def test_source_in_namespace_none_when_host_unresolvable(cfg):
    # An unknown host must fail closed, never fabricate a lookup for it — the
    # `cfg` fixture only configures a `[[git.endpoint]]` for HOST.
    mr_namespace = _mr_namespace(cfg)
    assert await mr_namespace.source_in_namespace("evil.example", "group/proj", 7) is None


async def test_source_in_namespace_cached_within_ttl_then_refetched(cfg, respx_router):
    now = {"t": 1000.0}
    mr_namespace = _mr_namespace(cfg, clock=lambda: now["t"])
    route = respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "claude/x", "author": {"id": 42}})
    )

    assert await mr_namespace.source_in_namespace(HOST, "group/proj", 7) is True
    assert await mr_namespace.source_in_namespace(HOST, "group/proj", 7) is True
    assert route.call_count == 1  # second call served from the 30s cache

    now["t"] += 31  # past the TTL
    assert await mr_namespace.source_in_namespace(HOST, "group/proj", 7) is True
    assert route.call_count == 2  # cache expired → refetched
