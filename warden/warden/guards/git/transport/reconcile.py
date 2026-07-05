"""git guard reconcile: rebuild the branch-quota counter from upstream truth.

Uses only the forge-neutral ``warden.core.transport`` — no
``guards.git.gitlab`` import, so the git guard's reconcile never depends on
the REST-API guard's own reconcile.
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


async def _list_agent_branches(upstream: Upstream, cfg: Config, pid: str) -> list[str]:
    branches = await get_paginated(upstream, f"projects/{pid}/repository/branches")
    return [b["name"] for b in branches if cfg.in_branch_namespace(b.get("name", ""))]


async def reconcile_branches(
    cfg: Config, router: UpstreamRouter, branch_state: BranchState
) -> bool:
    """Rebuild ``agent_branches`` for every allowed project, on every *open*
    configured endpoint. Returns True on full success.

    Iterates ``cfg.open_hosts`` (not ``cfg.effective_hosts``) — every
    configured endpoint whose ``access_mode`` is not ``"closed"``. A closed
    endpoint has no usable read credential, is unreachable via ``host_gate``
    (R6) anyway, and never needed reconciling. A single-endpoint deployment
    iterates that one host. The host×project loop and its fail-safe handling
    live in ``for_each_host_project`` (shared with the REST-API guard's
    ``reconcile_mrs``), which trusts that the ``hosts`` it is given are
    already open; this function supplies only the branch-listing/replace
    domain logic.
    """

    async def _reconcile_one(upstream: Upstream, host: str, project: str) -> None:
        pid = project_id(project)
        branches = await _list_agent_branches(upstream, cfg, pid)
        branch_state.replace_branches(host, project, branches)

    return await for_each_host_project(cfg, router, cfg.open_hosts, "git", _reconcile_one)
