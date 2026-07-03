"""REST-API guard reconcile (W8.2, §6.11, folded here in §07 Punkt 6 step 5
from the now-dissolved ``guards.gitlab.forge.GitForge``): rebuild the
MR-quota counter and the numeric-id project aliases (M6) from GitLab truth.

Implementation detail of the API guard, not a shared class — uses only the
forge-neutral :mod:`warden.core.transport`.
"""

from __future__ import annotations

import logging

from ...core.config import Config
from ...core.model import TokenKind
from ...core.transport import Upstream, get_paginated, project_id
from .state import MrState

log = logging.getLogger("warden")


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
    cfg: Config, upstream: Upstream, mr_state: MrState
) -> tuple[bool, set[str]]:
    """Rebuild ``agent_mrs`` and the numeric-id alias set for every allowed project.

    Returns ``(ok, resolved_ids)``. Fail-safe (§6.11): a project whose id
    resolution or MR listing fails leaves that project's row untouched and
    reports ``ok=False``, so the caller keeps the core lock rather than
    trusting an undercounted/stale view.
    """
    ok = True
    resolved_ids: set[str] = set()
    for project in cfg.allowed_projects:
        pid = project_id(project)
        try:
            numeric_id = await _resolve_project_id(upstream, pid)
            mrs = await _list_agent_mrs(upstream, cfg, pid)
        except Exception as exc:  # keep state locked on any failure
            log.error("api reconcile failed for %s: %s", project, exc)
            ok = False
            continue
        resolved_ids.add(numeric_id)
        mr_state.replace_mrs(project, mrs)
    return ok, resolved_ids
