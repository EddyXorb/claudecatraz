"""Core policy data types: pure values shared by kernel and every guard.

Kept guard-agnostic on purpose: no forge vocabulary (MR, ``iid``, ref) lives here,
only what the kernel pipeline and audit/state layers need from *any* intent.
Guard-specific intent shapes live with their guard and satisfy :class:`Intent` structurally.
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
    """Snapshot of quota counters. ``locked`` ⇒ fail-safe deny."""

    open_mrs: int = 0
    open_branches: int = 0
    writes_last_hour: int = 0
    locked: bool = False


class Intent(Protocol):
    """What every guard's parsed request must expose to the kernel.

    Deliberately minimal (read-only properties):

    * ``writes`` — derived by the guard's parser, never from a :class:`Decision`.
      Allows read-only mode-gate to run *before* ``enrich``, keeping credentials unreachable.
    * ``project`` — what the resource-allowlist gate needs.
    * ``method`` — the audit envelope's verb (HTTP method for REST, ``"push"`` for git).

    Guard-specific fields live on the concrete Intent dataclass in that guard's package.
    """

    @property
    def writes(self) -> bool: ...

    @property
    def project(self) -> str: ...

    @property
    def method(self) -> str: ...