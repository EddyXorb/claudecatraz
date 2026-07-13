"""git guard reconcile: rebuild the branch-quota counter from upstream truth.

Uses only the forge-neutral warden.core.transport, so this never depends
on the REST-API guard's own reconcile.
"""

from __future__ import annotations

from ....core.config import Config
from ....core.transport import (
    Upstream,
    UpstreamRouter,
    for_each_host_project,
    get_paginated,
    project_id,
)
from .state import BranchState


async def _list_agent_branches(upstream: Upstream, cfg: Config, host: str, pid: str) -> list[str]:
    branches = await get_paginated(upstream, f"projects/{pid}/repository/branches")
    return [b["name"] for b in branches if cfg.in_branch_namespace(host, b.get("name", ""))]


async def reconcile_branches(
    cfg: Config, router: UpstreamRouter, branch_state: BranchState
) -> bool:
    """Rebuild agent_branches for every allowed project, on every open
    configured endpoint. Returns True on full success.

    Iterates cfg.open_hosts, not cfg.effective_hosts — a closed endpoint
    has no usable read credential and never needs reconciling."""

    async def _reconcile_one(upstream: Upstream, host: str, project: str) -> None:
        pid = project_id(project)
        branches = await _list_agent_branches(upstream, cfg, host, pid)
        branch_state.replace_branches(host, project, branches)

    return await for_each_host_project(cfg, router, cfg.open_hosts, "git", _reconcile_one)
