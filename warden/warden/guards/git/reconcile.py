"""git guard reconcile (§07 Punkt 6, step 4; host dimension per §07 Punkt 8
follow-up): rebuild the branch-quota counter from upstream truth. Uses only
the forge-neutral :mod:`warden.core.transport` — no
``guards.gitlab``/``guards.gitlab_api`` import, so the git guard's reconcile
never depends on the REST-API guard's own reconcile.
"""

from __future__ import annotations

from ...core.config import Config
from ...core.transport import (
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
    """Rebuild ``agent_branches`` for every allowed project, on every
    configured host (§07 Punkt 8 follow-up, design spike section 4). Returns
    True on full success.

    ``cfg.effective_hosts`` is every configured ``[[git.endpoint]]``'s host
    (step 03) — a single-endpoint deployment iterates one host, identical
    behaviour to before the host dimension existed. The host×project loop and its fail-safe
    (§6.11) handling live in :func:`~warden.core.transport.for_each_host_project`
    (shared with the REST-API guard's :func:`~warden.guards.gitlab_api.reconcile.reconcile_mrs`);
    this function supplies only the branch-listing/replace domain logic.
    """

    async def _reconcile_one(upstream: Upstream, host: str, project: str) -> None:
        pid = project_id(project)
        branches = await _list_agent_branches(upstream, cfg, pid)
        branch_state.replace_branches(host, project, branches)

    return await for_each_host_project(cfg, router, "git", _reconcile_one)
