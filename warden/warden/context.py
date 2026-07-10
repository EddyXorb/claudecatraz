"""The root application context — logic-free on purpose.

Composition root: the one place that imports and wires concrete guard
classes, keeping app.py/__main__.py free of guard-policy internals.
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
from .guards.git.gitlab.guard import ApiGuard
from .guards.git.transport.guard import GitGuard


@dataclass
class AppContext:
    cfg: Config
    state: State
    audit: AuditLog
    router: UpstreamRouter
    guards: list[Guard[Any]]

    async def aclose(self) -> None:
        """Tear down every long-lived resource the composition root created.

        Bundled here so callers (__main__) never need to know the
        individual collaborators exist, let alone their shutdown order.
        """
        await self.router.aclose()
        await self.audit.stop()
        self.state.close()

    async def reconcile_all(self) -> bool:
        """Reconcile every guard; each unlocks only its own per-guard lock.

        Return value is only for the startup/periodic log line, not a gate:
        a guard whose upstream is unreachable stays fail-safe-locked and
        denies, while every other guard keeps serving its own fresh counts.
        """
        ok = True
        for g in self.guards:
            ok = (await g.reconcile()) and ok
        return ok


def build_context(
    cfg: Config, state: State, audit: AuditLog, *, client: Optional[httpx.AsyncClient] = None
) -> AppContext:
    """Assemble the transport and every shipped guard, then the root context.

    UpstreamRouter is shared (one connection pool) by the git and REST-API
    guards. client is a test-only escape hatch for a non-default
    httpx.AsyncClient; production callers omit it.
    """
    router = UpstreamRouter(cfg, client=client)
    guards: list[Guard[Any]] = [
        GitGuard(cfg, state, audit, router),
        ApiGuard(cfg, state, audit, router),
    ]
    return AppContext(cfg=cfg, state=state, audit=audit, router=router, guards=guards)
