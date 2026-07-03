"""The git guard's Intents: replaces the old channel-union ``ProxyRequest``.
Forge-agnostic — the only non-primitive field is a git-protocol concept
(``RefCommand``), never a forge one.

:class:`GitPushIntent` is a ``git-receive-pack`` request — always a write.
:class:`GitReadIntent` covers advertise/upload-pack (discovery and fetch);
``writes`` is normally ``False`` except for push discovery
(``?service=git-receive-pack``), which must still carry the write token.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from warden.core.model import Intent

from .pktline import RefCommand


@dataclass
class GitPushIntent(Intent):
    _project: str
    # Audit-facing verb (§03.2 core.model.Intent) — not an HTTP method; kept
    # as the pre-Schritt-5 literal ("push") for byte-compatible JSONL.
    _method: str = "push"
    ref_commands: list[RefCommand] = field(default_factory=list)
    # Plumbing `forward` needs to stream the *unchanged* body upstream
    # (SHA-preserving, W7.3) — not decision-relevant, just carried along.
    head: bytes = b""
    rest: Optional[AsyncIterator[bytes]] = None
    content_type: str = "application/x-git-receive-pack-request"
    extra_headers: dict[str, str] = field(default_factory=dict)
    sideband: bool = False

    @property
    def writes(self) -> bool:
        # The only intent this guard's kernel pipeline ever parses is a
        # receive-pack push — always a write by construction.
        return True

    @property
    def project(self) -> str:
        return self._project

    @property
    def method(self) -> str:
        return self._method


@dataclass
class GitReadIntent(Intent):
    """advertise / upload-pack: reads, except push discovery (see ``writes``)."""

    _project: str
    _method: str
    operation: str  # "advertise" | "upload-pack"
    service: str = "git-upload-pack"
    # Push discovery (advertise with ?service=git-receive-pack) must count as
    # a write: the write token it needs must never reach upstream in
    # read-only/off mode. Set by the guard's parse(), never derived here.
    _writes: bool = False

    @property
    def writes(self) -> bool:
        return self._writes

    @property
    def project(self) -> str:
        return self._project

    @property
    def method(self) -> str:
        return self._method
