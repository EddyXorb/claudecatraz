"""ownership.py (W6.2, §07 Punkt 4/6, §07 Punkt 8 follow-up): the REST-API guard's MR
source-branch-namespace rule + its cache.

Folded here from the now-dissolved ``guards.gitlab.forge.GitForge`` (§07
Punkt 6 step 5) — see ``test_api_reconcile.py`` for the reconcile/pagination
side.
"""

from __future__ import annotations

import httpx

from warden.core.transport import UpstreamRouter
from warden.guards.gitlab_api.ownership import MrOwnership

HOST = "gitlab.example"  # matches the `cfg` fixture's api_url host (conftest.py)


def _ownership(cfg, *, clock=None) -> MrOwnership:
    kwargs = {"clock": clock} if clock else {}
    return MrOwnership(UpstreamRouter(cfg), cfg, **kwargs)


async def test_source_in_namespace_true_when_prefixed_regardless_of_author(cfg, respx_router):
    # A namespace source_branch is enough — the author (a foreign colleague here)
    # no longer matters (§07 Punkt 4: blast-radius is the branch namespace).
    ownership = _ownership(cfg)
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "claude/x", "author": {"id": 999}})
    )
    assert await ownership.source_in_namespace(HOST, "group/proj", 7) is True


async def test_source_in_namespace_false_when_prefix_missing(cfg, respx_router):
    ownership = _ownership(cfg)
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "feature/x", "author": {"id": 42}})
    )
    assert await ownership.source_in_namespace(HOST, "group/proj", 7) is False


async def test_source_in_namespace_none_when_lookup_fails(cfg, respx_router):
    # None ⇒ the policy denies (default-deny holds); it must not be coerced to False/True.
    ownership = _ownership(cfg)
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(404)
    )
    assert await ownership.source_in_namespace(HOST, "group/proj", 7) is None


async def test_source_in_namespace_none_when_host_unresolvable(cfg):
    # An unknown host must fail closed, never fabricate a lookup for it.
    from dataclasses import replace

    multi_cfg = replace(
        cfg, host_order=("gitlab.example",), allowed_hosts=frozenset({"gitlab.example"})
    )
    ownership = _ownership(multi_cfg)
    assert await ownership.source_in_namespace("evil.example", "group/proj", 7) is None


async def test_source_in_namespace_cached_within_ttl_then_refetched(cfg, respx_router):
    now = {"t": 1000.0}
    ownership = _ownership(cfg, clock=lambda: now["t"])
    route = respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "claude/x", "author": {"id": 42}})
    )

    assert await ownership.source_in_namespace(HOST, "group/proj", 7) is True
    assert await ownership.source_in_namespace(HOST, "group/proj", 7) is True
    assert route.call_count == 1  # second call served from the 30s cache

    now["t"] += 31  # past the TTL
    assert await ownership.source_in_namespace(HOST, "group/proj", 7) is True
    assert route.call_count == 2  # cache expired → refetched
