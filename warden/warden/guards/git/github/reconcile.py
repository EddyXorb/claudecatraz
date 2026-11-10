"""GitHub REST guard reconcile: rebuild the open-MR counter from GitHub
truth, counting only pull requests that are the agent's own."""

from __future__ import annotations

from typing import Any

from ....core.config import Config
from ....core.model import TokenKind
from ....core.transport import Upstream, UpstreamRouter, for_each_host_project
from ..endpoints import open_hosts_composing
from ..mr_state import MrState
from .lookup import pull_is_agents, repo_path

# A self-referencing Link header must fail the reconcile, never hang it.
_MAX_PAGES = 1000


async def get_linked_pages(upstream: Upstream, path: str) -> list[Any]:
    """Every page of a GitHub list endpoint, following Link rel="next".

    A next link outside the endpoint's REST base raises — the read token
    never leaves the configured host."""
    items: list[Any] = []
    next_path = path
    for _ in range(_MAX_PAGES):
        resp = await upstream.get_json(next_path, TokenKind.READ)
        resp.raise_for_status()
        page = resp.json()
        if not isinstance(page, list):
            raise ValueError(f"expected a list from {next_path!r}")
        items.extend(page)
        next_url = resp.links.get("next", {}).get("url")
        if next_url is None:
            return items
        relative = upstream.rest_path_of(next_url)
        if relative is None:
            raise ValueError(f"pagination link leaves the REST base: {next_url!r}")
        next_path = relative
    raise ValueError(f"more than {_MAX_PAGES} pages from {path!r}")


async def reconcile_pulls(cfg: Config, router: UpstreamRouter, mr_state: MrState) -> bool:
    """Rebuild agent_mrs for every allowed repository on every open GitHub
    endpoint. Returns True only on full success."""

    async def _reconcile_one(upstream: Upstream, host: str, project: str) -> None:
        path = f"{repo_path(project)}/pulls?state=open&per_page=100"
        pulls = await get_linked_pages(upstream, path)
        mine = [(int(p["number"]), "opened") for p in pulls if pull_is_agents(cfg, host, p)]
        mr_state.replace_mrs(host, project, mine)

    hosts = open_hosts_composing(cfg, "github")
    return await for_each_host_project(cfg, router, hosts, "github", _reconcile_one)
