"""REST-API guard reconcile: rebuild the MR-quota counter and numeric-id
project aliases from GitLab truth. Uses only the forge-neutral transport
module, not a shared class.
"""

from __future__ import annotations

from typing import Any, Mapping

from ....core.config import Config
from ....core.model import TokenKind
from ....core.transport import Upstream, UpstreamRouter, for_each_host_project, project_id
from ..endpoints import open_hosts_composing
from ..mr_state import MrState


async def get_paginated(upstream: Upstream, path: str) -> list[Any]:
    """Fetch every page of a GitLab list endpoint, following X-Next-Page.

    Without this a project with >100 agent MRs would only count the first
    page, undercount the quota, and wrongly allow further writes."""
    items: list[Any] = []
    page = 1
    while page:
        sep = "&" if "?" in path else "?"
        resp = await upstream.get_json(f"{path}{sep}per_page=100&page={page}", TokenKind.READ)
        resp.raise_for_status()
        items.extend(resp.json())
        nxt = resp.headers.get("x-next-page", "")
        page = int(nxt) if nxt else 0
    return items


async def _resolve_project_id(upstream: Upstream, pid: str) -> str:
    """Map a url-encoded project path to its numeric id (GET /projects/:path)."""
    resp = await upstream.get_json(f"projects/{pid}", TokenKind.READ)
    resp.raise_for_status()
    return str(resp.json()["id"])


async def _list_agent_mrs(
    upstream: Upstream, cfg: Config, host: str, pid: str
) -> list[tuple[int, str]]:
    path = f"projects/{pid}/merge_requests?state=opened"
    mrs = await get_paginated(upstream, path)
    return [
        (int(m["iid"]), m.get("state", "opened"))
        for m in mrs
        if cfg.in_branch_namespace(host, m.get("source_branch", "") or "")
    ]


async def reconcile_mrs(
    cfg: Config, router: UpstreamRouter, mr_state: MrState
) -> tuple[bool, Mapping[str, set[str]]]:
    """Rebuild agent_mrs and the numeric-id alias map for every allowed
    project, on every open endpoint of a type this guard serves.

    resolved_ids is keyed by host, so one host's numeric id never aliases
    another's project. Returns (ok, resolved_ids)."""
    resolved_ids: dict[str, set[str]] = {}

    async def _reconcile_one(upstream: Upstream, host: str, project: str) -> None:
        pid = project_id(project)
        numeric_id = await _resolve_project_id(upstream, pid)
        mrs = await _list_agent_mrs(upstream, cfg, host, pid)
        resolved_ids.setdefault(host, set()).add(numeric_id)
        mr_state.replace_mrs(host, project, mrs)

    ok = await for_each_host_project(
        cfg, router, open_hosts_composing(cfg, "gitlab"), "api", _reconcile_one
    )
    return ok, resolved_ids
