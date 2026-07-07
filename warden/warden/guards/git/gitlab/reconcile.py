"""REST-API guard reconcile: rebuild the MR-quota counter and the numeric-id
project aliases from GitLab truth.

Implementation detail of the API guard, not a shared class — uses only the
forge-neutral transport module.
"""

from __future__ import annotations

from ....core.config import Config
from ....core.model import TokenKind
from ....core.transport import (
    Upstream,
    UpstreamRouter,
    for_each_host_project,
    get_paginated,
    project_id,
)
from .state import MrState


async def _resolve_project_id(upstream: Upstream, pid: str) -> str:
    """Map a url-encoded project path to its numeric id (GET /projects/:path)."""
    resp = await upstream.get_json(f"projects/{pid}", TokenKind.READ)
    resp.raise_for_status()
    return str(resp.json()["id"])


async def _list_agent_mrs(upstream: Upstream, cfg: Config, pid: str) -> list[tuple[int, str]]:
    path = f"projects/{pid}/merge_requests?state=opened"
    mrs = await get_paginated(upstream, path)
    return [
        (int(m["iid"]), m.get("state", "opened"))
        for m in mrs
        if cfg.in_branch_namespace(m.get("source_branch", "") or "")
    ]


async def reconcile_mrs(
    cfg: Config, router: UpstreamRouter, mr_state: MrState
) -> tuple[bool, set[str]]:
    """Rebuild agent_mrs and the numeric-id alias set for every allowed
    project, on every currently open configured endpoint.

    Iterates cfg.open_hosts (not cfg.effective_hosts) — see
    guards.git.reconcile.reconcile_branches's docstring for the full
    rationale, identical here. The numeric-id alias set is a plain union
    across hosts (project-id widening does not need to know which host an id
    came from — ApiGuard.project_allowed only asks "is this id known",
    never "on which host"). Returns (ok, resolved_ids). The host x
    project loop and its fail-safe handling live in
    core.transport.for_each_host_project (shared with the git guard's
    reconcile_branches), which trusts that the hosts it is given are
    already open; this function supplies only the
    id-resolution/MR-listing/replace domain logic.
    """
    resolved_ids: set[str] = set()

    async def _reconcile_one(upstream: Upstream, host: str, project: str) -> None:
        pid = project_id(project)
        numeric_id = await _resolve_project_id(upstream, pid)
        mrs = await _list_agent_mrs(upstream, cfg, pid)
        resolved_ids.add(numeric_id)
        mr_state.replace_mrs(host, project, mrs)

    ok = await for_each_host_project(cfg, router, cfg.open_hosts, "api", _reconcile_one)
    return ok, resolved_ids
