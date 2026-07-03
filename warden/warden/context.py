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
