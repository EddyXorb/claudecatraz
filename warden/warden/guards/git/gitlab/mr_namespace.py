"""MR source-branch-namespace lookup: an upstream GET, cached briefly.

Implementation detail of the REST-API guard, not a shared class — it needs
only the forge-neutral transport module to reach upstream.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from ....core.config import Config
from ....core.model import TokenKind
from ....core.transport import UpstreamRouter, project_id


class MrNamespace:
    """Credential-backed source_branch lookup, with a short-TTL cache
    (performance only, never security). Resolves the upstream per call from
    the raw Host header; the cache key includes the canonical host so two
    hosts sharing a project path/iid never share an entry.
    """

    def __init__(
        self, router: UpstreamRouter, cfg: Config, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._router = router
        self._cfg = cfg
        self._clock = clock
        self._cache: dict[tuple[str, str, int], tuple[bool, float]] = {}
        self._ttl = 30.0

    async def source_in_namespace(self, host: str, project: str, iid: int) -> Optional[bool]:
        """True iff the MR's source_branch lies in the allowed branch namespace.

        Author-independent by design: the branch namespace is the agent's
        exclusive push area, not who opened the MR. Returns None
        (default-deny) when the host is unresolvable or the lookup fails."""
        upstream = self._router.resolve(host)
        canonical = self._cfg.resolve_target_host(host)
        if upstream is None or canonical is None:
            return None  # unresolved host — should not happen past kernel_gates' host_gate

        key = (canonical, project, iid)
        cached = self._cache.get(key)
        if cached is not None and cached[1] > self._clock():
            return cached[0]

        resp = await upstream.get_json(
            f"projects/{project_id(project)}/merge_requests/{iid}", TokenKind.READ
        )
        if resp.status_code != 200:
            return None
        mr = resp.json()
        source = mr.get("source_branch", "") or ""
        ok = self._cfg.in_branch_namespace(source)
        self._cache[key] = (ok, self._clock() + self._ttl)
        return ok
