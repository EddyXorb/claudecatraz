"""Core policy data types: pure values shared by kernel and every guard.

Kept guard-agnostic: no forge vocabulary (MR, iid, ref) lives here, only
what the kernel pipeline and audit/state layers need from any intent.
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
    reason: str
    token: TokenKind = TokenKind.NONE  # which upstream token, if allow


@dataclass(frozen=True)
class StateView:
    """Snapshot of quota counters. locked ⇒ fail-safe deny."""

    open_mrs: int = 0
    open_branches: int = 0
    writes_last_hour: int = 0
    locked: bool = False


class Intent(Protocol):
    """What every guard's parsed request must expose to the kernel.

    needs_write is the credential axis, not "changes state": git push
    discovery reads refs but still needs the write token, so needs_write is
    True for it — this lets the write-credential gate run before enrich.
    """

    @property
    def needs_write(self) -> bool: ...

    @property
    def project(self) -> str: ...

    @property
    def method(self) -> str: ...

    @property
    def host(self) -> str: ...
