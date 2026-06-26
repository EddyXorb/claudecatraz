"""Shared runtime context + reconcile logic (W6.2, W8.2, §6.11).

Holds the long-lived collaborators (config, upstream, state, audit) plus the
service-account id and the short-lived MR-ownership cache. Reconcile rebuilds
the quota counters from GitLab truth — at startup (before the agent port opens)
and periodically as the backstop.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Callable, Optional

from .audit import AuditLog
from .config import Config
from .model import TokenKind
from .state import State
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
        """Resolve and cache the write-token's user id once (W6.2)."""
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
    async def mr_owned_by_claude(self, project: str, iid: int) -> Optional[bool]:
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
        ok = source.startswith(self.cfg.branch_prefix) and sa is not None and author_id == sa
        self._owner_cache[key] = (ok, self._clock() + self._owner_ttl)
        return ok

    # --- reconcile (W8.2) ------------------------------------------------------
    async def reconcile(self) -> bool:
        """Rebuild branch/MR counters from GitLab. Returns True on full success."""
        sa = await self.resolve_service_account()
        ok = True
        for project in self.cfg.allowed_projects:
            pid = project_id(project)
            try:
                branches = await self._list_claude_branches(pid)
                mrs = await self._list_claude_mrs(pid, sa)
            except Exception as exc:  # keep state locked on any failure
                print(f"warden: reconcile failed for {project}: {exc}", file=sys.stderr)
                ok = False
                continue
            self.state.replace_branches(project, branches)
            self.state.replace_mrs(project, mrs)
        if ok:
            self.state.mark_reconciled()
        return ok

    async def _get_paginated(self, path: str) -> list[Any]:
        """Fetch every page of a GitLab list endpoint (W8.2).

        Without this a project with >100 claude branches/MRs would only count the
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

    async def _list_claude_branches(self, pid: str) -> list[str]:
        branches = await self._get_paginated(f"projects/{pid}/repository/branches")
        return [
            b["name"]
            for b in branches
            if b.get("name", "").startswith(self.cfg.branch_prefix)
        ]

    async def _list_claude_mrs(self, pid: str, sa: Optional[int]) -> list[tuple[int, str]]:
        path = f"projects/{pid}/merge_requests?state=opened"
        if sa is not None:
            path += f"&author_id={sa}"
        mrs = await self._get_paginated(path)
        return [
            (int(m["iid"]), m.get("state", "opened"))
            for m in mrs
            if (m.get("source_branch", "") or "").startswith(self.cfg.branch_prefix)
        ]
