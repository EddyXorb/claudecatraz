"""The GitLab forge: credentials, service-account, MR-ownership and reconcile
(W6.2, W8.2, §6.11).

:class:`GitForge` holds the long-lived collaborators (config, upstream,
state, audit) plus the service-account id, the short-lived MR-ownership
cache, and the numeric project-id aliases reconcile resolves. Reconcile
rebuilds the quota counters from GitLab truth — at startup (before the agent
port opens) and periodically as the backstop. Shared by both the git guard
and the REST-API guard (§03.5/03.6): each of them owns its own
:class:`~warden.core.guard.Guard` instance, but both need this same forge
state (credentials, ownership, reconcile) — that is what makes it a separate,
guard-agnostic collaborator instead of living inside either guard.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Callable, Optional

from ...core.audit import AuditLog
from ...core.config import Config, normalize_project
from ...core.model import StateView, TokenKind
from ...core.state import State
from .state import ForgeState
from .upstream import Upstream, project_id


# Generic Forge class, a forge is git + nice accessors around it, such as gitlab, github, codeberg,..
class GitForge:
    def __init__(
        self,
        cfg: Config,
        upstream: Upstream,
        state: State,
        audit: AuditLog,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = cfg
        self.upstream = upstream
        self.state = state
        self.forge_state = ForgeState(state.store)
        self.audit = audit
        self._clock = clock
        self.service_account_id: Optional[int] = None
        # (project, iid) -> (ok, expires_at). Performance only, never security.
        self._owner_cache: dict[tuple[str, int], tuple[bool, float]] = {}
        self._owner_ttl = 30.0
        # Numeric-id aliases of cfg.allowed_projects, resolved at reconcile (M6).
        # Forge state, not Config — Config stays immutable for the life of the
        # process; only the forge's view of "which ids currently alias an
        # allowlisted project" is ever refreshed.
        self.project_id_aliases: set[str] = set()

    # --- service account -------------------------------------------------------
    async def resolve_service_account(self) -> Optional[int]:
        """Resolve and cache the write-token's user id once (W6.2).

        Returns None immediately when writes are disabled — the (possibly empty)
        write token must never be sent upstream in off/read-only mode.
        """
        if not self.cfg.writes_enabled:
            return None
        if self.service_account_id is not None:
            return self.service_account_id
        resp = await self.upstream.get_json("user", TokenKind.WRITE)
        if resp.status_code == 200:
            self.service_account_id = int(resp.json()["id"])
        else:
            print(
                f"warden: could not resolve service account (GET /user → {resp.status_code})",
                file=sys.stderr,
            )
        return self.service_account_id

    # --- ownership (W6.2) ------------------------------------------------------
    async def mr_owned_by_agent(self, project: str, iid: int) -> Optional[bool]:
        """True iff the MR is prefixed AND authored by the service account.

        Returns None when the lookup fails (→ policy denies, default-deny holds).
        """
        key = (project, iid)
        cached = self._owner_cache.get(key)
        if cached is not None and cached[1] > self._clock():
            return cached[0]

        sa = await self.resolve_service_account()
        resp = await self.upstream.get_json(
            f"projects/{project_id(project)}/merge_requests/{iid}", TokenKind.READ
        )
        if resp.status_code != 200:
            return None
        mr = resp.json()
        source = mr.get("source_branch", "") or ""
        author_id = (mr.get("author") or {}).get("id")
        ok = self.cfg.in_branch_namespace(source) and sa is not None and author_id == sa
        self._owner_cache[key] = (ok, self._clock() + self._owner_ttl)
        return ok

    # --- resource allowlist (M6) ------------------------------------------------
    def project_allowed_by_id(self, project: str) -> bool:
        """True iff ``project`` names the numeric-id alias of an allowlisted
        project, resolved by the last successful :meth:`reconcile`."""
        return normalize_project(project) in self.project_id_aliases

    # --- state view (§E) ---------------------------------------------------------
    def state_view(self) -> StateView:
        """Combined snapshot: core's fail-safe lock/writes counter plus this
        domain's branch/MR counts — what :class:`~warden.core.guard.Guard`
        subclasses that depend on this forge (git, REST) pass to ``decide``."""
        if not self.state.is_reconciled():
            return StateView(locked=True)
        return StateView(
            open_mrs=self.forge_state.open_mrs(),
            open_branches=self.forge_state.open_branches(),
            writes_last_hour=self.state.writes_last_hour(),
            locked=False,
        )

    # --- reconcile (W8.2) ------------------------------------------------------
    async def reconcile(self) -> bool:
        """Rebuild branch/MR counters from GitLab. Returns True on full success.

        In ``off`` mode no upstream call is made — the warden marks itself
        reconciled/unlocked so it can serve (and then deny) requests without ever
        contacting GitLab. Both tokens are empty in ``off``; no upstream call may
        happen.
        """
        if not self.cfg.gitlab_enabled:
            # off mode: skip all upstream calls; mark reconciled so the warden
            # opens the agent port and denies ops (instead of staying fail-safe locked).
            self.state.mark_reconciled()
            return True

        sa = await self.resolve_service_account()
        ok = True
        resolved_ids: list[str] = []
        for project in self.cfg.allowed_projects:
            pid = project_id(project)
            try:
                numeric_id = await self._resolve_project_id(pid)
                branches = await self._list_agent_branches(pid)
                mrs = await self._list_agent_mrs(pid, sa)
            except Exception as exc:  # keep state locked on any failure
                print(f"warden: reconcile failed for {project}: {exc}", file=sys.stderr)
                ok = False
                continue
            resolved_ids.append(numeric_id)
            self.forge_state.replace_branches(project, branches)
            self.forge_state.replace_mrs(project, mrs)
        # Teach the allowlist the numeric-id alias of each project so requests that
        # address /projects/<id>/… (instead of the path) are not wrongly R6-denied.
        # Forge state (project_id_aliases), never Config — Config is never mutated.
        self.project_id_aliases = set(resolved_ids)
        if ok:
            self.state.mark_reconciled()
        return ok

    async def _resolve_project_id(self, pid: str) -> str:
        """Map a url-encoded project path to its numeric id (GET /projects/:path)."""
        resp = await self.upstream.get_json(f"projects/{pid}", TokenKind.READ)
        resp.raise_for_status()
        return str(resp.json()["id"])

    async def _get_paginated(self, path: str) -> list[Any]:
        """Fetch every page of a GitLab list endpoint (W8.2).

        Without this a project with >100 agent branches/MRs would only count the
        first page, undercount the quota, and wrongly ``allow`` further writes.
        Follows the ``X-Next-Page`` header until it is empty.
        """
        items: list[Any] = []
        page = 1
        while page:
            sep = "&" if "?" in path else "?"
            resp = await self.upstream.get_json(
                f"{path}{sep}per_page=100&page={page}", TokenKind.READ
            )
            resp.raise_for_status()
            items.extend(resp.json())
            nxt = resp.headers.get("x-next-page", "")
            page = int(nxt) if nxt else 0
        return items

    async def _list_agent_branches(self, pid: str) -> list[str]:
        branches = await self._get_paginated(f"projects/{pid}/repository/branches")
        return [b["name"] for b in branches if self.cfg.in_branch_namespace(b.get("name", ""))]

    async def _list_agent_mrs(self, pid: str, sa: Optional[int]) -> list[tuple[int, str]]:
        path = f"projects/{pid}/merge_requests?state=opened"
        if sa is not None:
            path += f"&author_id={sa}"
        mrs = await self._get_paginated(path)
        return [
            (int(m["iid"]), m.get("state", "opened"))
            for m in mrs
            if self.cfg.in_branch_namespace(m.get("source_branch", "") or "")
        ]
