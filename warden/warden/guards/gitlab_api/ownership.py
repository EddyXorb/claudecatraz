"""MR source-branch-namespace lookup (§07 Punkt 4, folded here in §07 Punkt 6
step 5 from the now-dissolved ``guards.gitlab.forge.GitForge``).

Implementation detail of the REST-API guard, not a shared class — it needs
only the forge-neutral :mod:`warden.core.transport` to reach upstream.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from ...core.config import Config
from ...core.model import TokenKind
from ...core.transport import Upstream, project_id


class MrOwnership:
    """Credential-backed ``source_branch`` lookup, with a short-TTL cache
    (performance only, never security — a cache hit still reflects a lookup
    made within the last :attr:`_ttl` seconds)."""

    def __init__(
        self, upstream: Upstream, cfg: Config, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._upstream = upstream
        self._cfg = cfg
        self._clock = clock
        self._cache: dict[tuple[str, int], tuple[bool, float]] = {}
        self._ttl = 30.0

    async def source_in_namespace(self, project: str, iid: int) -> Optional[bool]:
        """True iff the MR's ``source_branch`` lies in the allowed branch namespace.

        Author-independent by design (§07 Punkt 4): blast-radius containment is
        the branch namespace, not who opened the MR — a namespace branch is the
        agent's exclusive push area regardless of author.

        Returns None when the lookup fails (→ policy denies, default-deny holds).
        """
        key = (project, iid)
        cached = self._cache.get(key)
        if cached is not None and cached[1] > self._clock():
            return cached[0]

        resp = await self._upstream.get_json(
            f"projects/{project_id(project)}/merge_requests/{iid}", TokenKind.READ
        )
        if resp.status_code != 200:
            return None
        mr = resp.json()
        source = mr.get("source_branch", "") or ""
        ok = self._cfg.in_branch_namespace(source)
        self._cache[key] = (ok, self._clock() + self._ttl)
        return ok
