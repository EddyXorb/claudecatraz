"""git guard reconcile (§07 Punkt 6, step 4): rebuild the branch-quota counter
from upstream truth. Uses only the forge-neutral :mod:`warden.core.transport`
— no ``guards.gitlab``/``guards.gitlab_api`` import, so the git guard's
reconcile never depends on the REST-API guard's own reconcile.
"""

from __future__ import annotations

import logging

from ...core.config import Config
from ...core.transport import Upstream, get_paginated, project_id
from .state import BranchState

log = logging.getLogger("warden")


async def _list_agent_branches(upstream: Upstream, cfg: Config, pid: str) -> list[str]:
    branches = await get_paginated(upstream, f"projects/{pid}/repository/branches")
    return [b["name"] for b in branches if cfg.in_branch_namespace(b.get("name", ""))]


async def reconcile_branches(cfg: Config, upstream: Upstream, branch_state: BranchState) -> bool:
    """Rebuild ``agent_branches`` for every allowed project. Returns True on full success.

    Fail-safe (§6.11): a project whose branch listing fails leaves that
    project's counter untouched and the overall result False, so the caller
    keeps the state locked instead of trusting an undercounted/stale view.
    """
    ok = True
    for project in cfg.allowed_projects:
        pid = project_id(project)
        try:
            branches = await _list_agent_branches(upstream, cfg, pid)
        except Exception as exc:  # keep state locked on any failure
            log.error("git reconcile failed for %s: %s", project, exc)
            ok = False
            continue
        branch_state.replace_branches(project, branches)
    return ok
