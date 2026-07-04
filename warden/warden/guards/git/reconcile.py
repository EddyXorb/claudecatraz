"""git guard reconcile (§07 Punkt 6, step 4; host dimension per §07 Punkt 8
follow-up): rebuild the branch-quota counter from upstream truth. Uses only
the forge-neutral :mod:`warden.core.transport` — no
``guards.gitlab``/``guards.gitlab_api`` import, so the git guard's reconcile
never depends on the REST-API guard's own reconcile.
"""

from __future__ import annotations

import logging

from ...core.config import Config
from ...core.transport import Upstream, UpstreamRouter, get_paginated, project_id
from .state import BranchState

log = logging.getLogger("warden")


async def _list_agent_branches(upstream: Upstream, cfg: Config, pid: str) -> list[str]:
    branches = await get_paginated(upstream, f"projects/{pid}/repository/branches")
    return [b["name"] for b in branches if cfg.in_branch_namespace(b.get("name", ""))]


async def reconcile_branches(
    cfg: Config, router: UpstreamRouter, branch_state: BranchState
) -> bool:
    """Rebuild ``agent_branches`` for every allowed project, on every
    configured host (§07 Punkt 8 follow-up, design spike section 4). Returns
    True on full success.

    ``cfg.effective_hosts`` is single-element (the implicit host) when
    multi-target is inactive — identical iteration count/behaviour to before
    the host dimension existed. Fail-safe (§6.11): a project whose branch
    listing fails on a given host leaves that ``(host, project)`` counter
    untouched and the overall result False, so the caller keeps the state
    locked instead of trusting an undercounted/stale view.
    """
    ok = True
    for host in cfg.effective_hosts:
        upstream = router.for_host(host)
        for project in cfg.allowed_projects:
            pid = project_id(project)
            try:
                branches = await _list_agent_branches(upstream, cfg, pid)
            except Exception as exc:  # keep state locked on any failure
                log.error("git reconcile failed for %s@%s: %s", project, host, exc)
                ok = False
                continue
            branch_state.replace_branches(host, project, branches)
    return ok
