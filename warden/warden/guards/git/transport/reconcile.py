"""git guard reconcile: rebuild the branch-quota counter from upstream truth.

Reads the ref advertisement over git Smart-HTTP itself, so it works for
every endpoint type and never depends on a forge's REST surface.
"""

from __future__ import annotations

from ....core.config import Config
from ....core.model import TokenKind
from ....core.transport import Upstream, UpstreamRouter, for_each_host_project
from .pktline import advertised_branches
from .state import BranchState


async def _list_agent_branches(
    upstream: Upstream, cfg: Config, host: str, project: str
) -> list[str]:
    resp = await upstream.git_get(
        project, "info/refs", params={"service": "git-upload-pack"}, token=TokenKind.READ
    )
    resp.raise_for_status()
    return [b for b in advertised_branches(resp.content) if cfg.in_branch_namespace(host, b)]


async def reconcile_branches(
    cfg: Config, router: UpstreamRouter, branch_state: BranchState
) -> bool:
    """Rebuild agent_branches for every allowed project, on every open
    configured endpoint. Returns True on full success."""

    async def _reconcile_one(upstream: Upstream, host: str, project: str) -> None:
        branches = await _list_agent_branches(upstream, cfg, host, project)
        branch_state.replace_branches(host, project, branches)

    return await for_each_host_project(cfg, router, cfg.open_hosts, "git", _reconcile_one)
