"""The root application context — logic-free on purpose.

Plain bag of long-lived collaborators every guard is assembled from, plus guards themselves.
Composition root: the ONE place that imports and wires concrete guard classes,
keeping app.py/__main__.py free of guard-policy internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .core.audit import AuditLog
from .core.config import Config
from .core.guard import Guard
from .core.state import State
from .core.transport import UpstreamRouter
from .guards.git.guard import GitGuard
from .guards.gitlab_api.guard import ApiGuard, GraphqlGuard


@dataclass
class AppContext:
    cfg: Config
    state: State
    audit: AuditLog
    router: UpstreamRouter
    guards: list[Guard[Any]]

    async def aclose(self) -> None:
        """Tear down every long-lived resource the composition root created.

        Bundled here so callers (``__main__``) never need to know the
        individual collaborators exist, let alone their shutdown order.
        """
        await self.router.aclose()
        await self.audit.stop()
        self.state.close()

    async def reconcile_all(self) -> bool:
        """Reconcile every guard, then mark the shared core lock reconciled
        **iff all guards succeeded**.

        The lock is a single, shared, global fail-safe: ``state.view().locked``
        stays true until the *whole* system has a fresh view. A guard may only
        rebuild its own domain counters here — it must not touch the shared lock
        alone, or one guard's success (say the git branch reconcile) would unlock
        the fail-safe while another guard's counters (the REST-API guard's MR
        count) were left stale by a failed reconcile, letting it serve and quota
        against an empty view. Only this orchestrator sees every guard, so only it
        may set the lock. Once set (persisted), a later transient per-guard failure
        does not re-lock — the latch means "has fully reconciled at least once".
        """
        ok = True
        for g in self.guards:
            ok = (await g.reconcile()) and ok
        if ok:
            self.state.mark_reconciled()
        return ok


def build_context(cfg: Config, state: State, audit: AuditLog) -> AppContext:
    """Assemble the transport and every shipped guard, then the root context.

    The one place that decides which guards exist and what each is given —
    a guard sees only the collaborators its own ``__init__`` declares (e.g.
    ``GraphqlGuard`` gets no transport at all, since it never contacts
    upstream). ``UpstreamRouter`` is forge-neutral, host-aware transport
    (§07 Punkt 6, §07 Punkt 8 follow-up), built here and shared (one
    connection pool, regardless of host count) by the git guard and the
    REST-API guard — neither guard depends on the other to reach it.
    """
    router = UpstreamRouter(cfg)
    guards: list[Guard[Any]] = [
        GitGuard(cfg, state, audit, router),
        ApiGuard(cfg, state, audit, router),
        GraphqlGuard(cfg, state, audit),
    ]
    return AppContext(cfg=cfg, state=state, audit=audit, router=router, guards=guards)
