"""MR source-branch-namespace lookup (§07 Punkt 4, folded here in §07 Punkt 6
step 5 from the now-dissolved ``guards.gitlab.forge.GitForge``; host dimension
per §07 Punkt 8 follow-up).

Implementation detail of the REST-API guard, not a shared class — it needs
only the forge-neutral :mod:`warden.core.transport` to reach upstream.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from ...core.config import Config
from ...core.model import TokenKind
from ...core.transport import UpstreamRouter, project_id


class MrOwnership:
    """Credential-backed ``source_branch`` lookup, with a short-TTL cache
    (performance only, never security — a cache hit still reflects a lookup
    made within the last :attr:`_ttl` seconds).

    Resolves the ``Upstream`` per call from the raw ``Host`` header via
    :class:`~warden.core.transport.UpstreamRouter` (§07 Punkt 8 follow-up) —
    the MR being checked lives on whichever host the current request
    targeted, never a fixed one from construction time. The cache key
    includes the canonical host so two hosts sharing a project path/iid never
    share a cache entry.
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
        """True iff the MR's ``source_branch`` lies in the allowed branch namespace.

        Author-independent by design (§07 Punkt 4): blast-radius containment is
        the branch namespace, not who opened the MR — a namespace branch is the
        agent's exclusive push area regardless of author.

        ``host`` is the raw ``Host`` header (as carried on ``Intent.host``) —
        resolved here to both the canonical cache-key host and the request's
        ``Upstream``. Returns None when the host is unresolvable or the
        lookup fails (→ policy denies, default-deny holds).
        """
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
