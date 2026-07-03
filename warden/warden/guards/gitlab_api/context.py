"""Shared runtime context + reconcile logic (W6.2, W8.2, §6.11).

Holds the long-lived collaborators (config, upstream, state, audit) plus the
service-account id and the short-lived MR-ownership cache. Reconcile rebuilds
the quota counters from GitLab truth — at startup (before the agent port opens)
and periodically as the backstop.

GitLab-specific (§03.3: assigned to ``guards/gitlab_api`` — MR ownership and
reconcile-against-GitLab-projects are forge concepts). One honest scope note
for this migration step: the git guard also holds a reference to this same
context object today, for the collaborators it *does* need (``cfg``,
``state``, ``upstream``, ``audit``) — turning reconcile into a formal
per-guard ``Guard`` method (§03.5) and giving each guard its own, narrower
context is out of scope for Migrationsschritt 5 (kernel extraction +
intent-split only); it is the explicit subject of §03.5/03.6 (Schritt 9/10).
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from typing import Any, Callable, Optional

from ...core.audit import AuditLog
from ...core.config import Config
from ...core.model import TokenKind
from ...core.state import State
from .upstream import Upstream, project_id


class AppContext:
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
        self.audit = audit
        self._clock = clock
        self.service_account_id: Optional[int] = None
        # (project, iid) -> (ok, expires_at). Performance only, never security.
        self._owner_cache: dict[tuple[str, int], tuple[bool, float]] = {}
        self._owner_ttl = 30.0

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
            self.state.replace_branches(project, branches)
            self.state.replace_mrs(project, mrs)
        # Teach the allowlist the numeric-id alias of each project so requests that
        # address /projects/<id>/… (instead of the path) are not wrongly R6-denied.
        self.cfg = replace(self.cfg, allowed_project_ids=tuple(resolved_ids))
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
        return [
            b["name"]
            for b in branches
            if self.cfg.in_branch_namespace(b.get("name", ""))
        ]

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
