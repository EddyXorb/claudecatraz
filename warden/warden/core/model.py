"""Core policy data types (W5): the pure values shared by the kernel and every
guard (§03.2/03.3, docs/design/architecture-generalization/03-guard-architektur.md).

Kept guard-agnostic on purpose (§03.3: "Kernel kennt keine GitLab-Begriffe") —
no forge vocabulary (MR, ``iid``, ref) lives here, only what the kernel's
pipeline (:mod:`core.guard`) and the audit/state layers need from *any*
intent, regardless of which guard produced it. Guard-specific intent shapes
(``guards.git.intent.GitIntent``, ``guards.gitlab_api.intent.ApiIntent``)
live with their guard and only need to satisfy :class:`Intent` structurally.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class TokenKind(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    NONE = "NONE"


@dataclass(frozen=True)
class Decision:
    allow: bool
    rule: str  # bare rule id ("R0".."R6") — sourced from core.rules, for the audit log
    reason: str
    token: TokenKind = TokenKind.NONE  # which upstream token, if allow


@dataclass(frozen=True)
class StateView:
    """Snapshot of the quota counters (W5). ``locked`` ⇒ fail-safe deny (§6.11)."""

    open_mrs: int = 0
    open_branches: int = 0
    writes_last_hour: int = 0
    locked: bool = False


class Intent(Protocol):
    """What every guard's parsed request must expose to the kernel (§03.2/03.3).

    Deliberately minimal (all read-only properties, satisfied structurally by
    a plain dataclass field or a computed property alike):

    * ``writes`` — derived by the guard's own parser (F3: "vom Parser
      abgeleitet, NICHT von der Decision"), never computed from a
      :class:`Decision`. This is what lets :meth:`core.guard.Guard.handle`
      enforce the read-only mode-gate *before* ``enrich`` runs, so a write
      credential is structurally unreachable in read-only/off mode.
    * ``project`` — what the resource-allowlist gate (M6,
      :func:`core.guard.project_gate`) needs.
    * ``method`` — the audit envelope's verb. For a REST guard this is the
      literal HTTP method; a non-REST guard (git) uses whatever short label
      its own audit trail used before this split (``"push"``) — the kernel
      never interprets this value, only logs it.

    Everything guard-specific (git's ``ref_commands``, the REST guard's
    ``path``/``fields``/``endpoint``) lives on the concrete Intent dataclass
    in that guard's own package — the kernel never reaches for those fields.
    """

    @property
    def writes(self) -> bool: ...

    @property
    def project(self) -> str: ...

    @property
    def method(self) -> str: ...