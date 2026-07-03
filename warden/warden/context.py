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
from .guards.git.guard import GitGuard
from .guards.gitlab.forge import GitForge
from .guards.gitlab.upstream import Upstream
from .guards.gitlab_api.guard import ApiGuard, GraphqlGuard


@dataclass
class AppContext:
    cfg: Config
    state: State
    audit: AuditLog
    upstream: Upstream
    forge: GitForge
    guards: list[Guard[Any]]

    async def aclose(self) -> None:
        """Tear down every long-lived resource the composition root created.

        Bundled here so callers (``__main__``) never need to know the
        individual collaborators exist, let alone their shutdown order.
        """
        await self.upstream.aclose()
        await self.audit.stop()
        self.state.close()


def build_context(cfg: Config, state: State, audit: AuditLog) -> AppContext:
    """Assemble the upstream, forge and every shipped guard, then the root context.

    The one place that decides which guards exist and what each is given —
    a guard sees only the collaborators its own ``__init__`` declares (e.g.
    ``GraphqlGuard`` gets no ``forge`` at all, since it never contacts
    upstream). ``Upstream`` is gitlab-specific transport, so it is built here
    rather than by the caller — nothing outside the composition root needs to
    know it exists.
    """
    upstream = Upstream(cfg)
    forge = GitForge(cfg, upstream, state, audit)
    guards: list[Guard[Any]] = [
        GitGuard(cfg, state, audit, forge),
        ApiGuard(cfg, state, audit, forge),
        GraphqlGuard(cfg, state, audit),
    ]
    return AppContext(
        cfg=cfg, state=state, audit=audit, upstream=upstream, forge=forge, guards=guards
    )
