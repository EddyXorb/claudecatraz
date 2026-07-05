"""The root application context — logic-free on purpose.

Plain bag of long-lived collaborators every guard is assembled from, plus guards themselves.
Composition root: the ONE place that imports and wires concrete guard classes,
keeping app.py/__main__.py free of guard-policy internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .core.audit import AuditLog
from .core.config import Config
from .core.guard import Guard
from .core.state import State
from .core.transport import UpstreamRouter
from .guards.git.transport.guard import GitGuard
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
        """Reconcile every guard, each unlocking only its own per-guard lock on
        success (see :meth:`~warden.core.state.State.is_reconciled`). Returns
        True iff *all* guards succeeded — used only for the startup/periodic log
        line, not to gate anything: the locks are per guard, so a guard whose
        upstream is permanently unreachable stays fail-safe-locked and denies,
        while every other guard keeps serving off its own fresh counts.

        Each guard's lock is a one-way latch: once a guard has reconciled once,
        a later transient failure on a periodic cycle does not re-lock it — it
        keeps serving its last known-good counts instead of flapping locked on a
        blip; the next successful cycle refreshes them.
        """
        ok = True
        for g in self.guards:
            ok = (await g.reconcile()) and ok
        return ok


def build_context(
    cfg: Config, state: State, audit: AuditLog, *, client: Optional[httpx.AsyncClient] = None
) -> AppContext:
    """Assemble the transport and every shipped guard, then the root context.

    The one place that decides which guards exist and what each is given —
    a guard sees only the collaborators its own ``__init__`` declares (e.g.
    ``GraphqlGuard`` gets no transport at all, since it never contacts
    upstream). ``UpstreamRouter`` is forge-neutral, host-aware transport
    (§07 Punkt 6, §07 Punkt 8 follow-up), built here and shared (one
    connection pool, regardless of host count) by the git guard and the
    REST-API guard — neither guard depends on the other to reach it.

    ``client`` is an escape hatch for tests that need a non-default
    ``httpx.AsyncClient`` (e.g. a real end-to-end test whose upstream serves
    a self-signed cert); every production caller omits it and gets
    ``UpstreamRouter``'s own default client.
    """
    router = UpstreamRouter(cfg, client=client)
    guards: list[Guard[Any]] = [
        GitGuard(cfg, state, audit, router),
        ApiGuard(cfg, state, audit, router),
        GraphqlGuard(cfg, state, audit),
    ]
    return AppContext(cfg=cfg, state=state, audit=audit, router=router, guards=guards)
