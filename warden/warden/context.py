"""The root application context (§03.5/03.6) — logic-free on purpose.

:class:`AppContext` is a plain bag of the long-lived collaborators every
guard is assembled from, plus the assembled guards themselves; it holds no
behaviour of its own (that lives in :class:`~warden.guards.gitlab.forge.GitlabForge`
and in each :class:`~warden.core.guard.Guard`). :func:`build_context` is the
composition root: the ONE place that imports the concrete guard classes
(:class:`~warden.guards.git.guard.GitGuard`,
:class:`~warden.guards.gitlab_api.guard.ApiGuard`/:class:`~warden.guards.gitlab_api.guard.GraphqlGuard`)
and wires them up — keeping ``app.py``/``__main__.py`` free of guard-policy
internals; they only ever see ``ctx.guards`` (generic ``Guard`` instances).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .core.audit import AuditLog
from .core.config import Config
from .core.guard import Guard
from .core.state import State
from .guards.git.guard import GitGuard
from .guards.gitlab.forge import GitlabForge
from .guards.gitlab.upstream import Upstream
from .guards.gitlab_api.guard import ApiGuard, GraphqlGuard


@dataclass
class AppContext:
    cfg: Config
    state: State
    audit: AuditLog
    forge: GitlabForge
    guards: list[Guard[Any]]


def build_context(cfg: Config, upstream: Upstream, state: State, audit: AuditLog) -> AppContext:
    """Assemble the forge and every shipped guard, then the root context.

    The one place that decides which guards exist and what each is given —
    a guard sees only the collaborators its own ``__init__`` declares (e.g.
    ``GraphqlGuard`` gets no ``forge`` at all, since it never contacts
    upstream).
    """
    forge = GitlabForge(cfg, upstream, state, audit)
    guards: list[Guard[Any]] = [
        GitGuard(cfg, state, audit, forge),
        ApiGuard(cfg, state, audit, forge),
        GraphqlGuard(cfg, state, audit),
    ]
    return AppContext(cfg=cfg, state=state, audit=audit, forge=forge, guards=guards)
