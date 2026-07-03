"""The git guard's Intent: forge-agnostic, covers all three git Smart-HTTP
operations (advertise, upload-pack, receive-pack) dispatched on ``operation``.

``writes`` is False except for receive-pack push and push discovery
(advertise with ?service=git-receive-pack), both of which must carry the write token.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from warden.core.model import Intent

from .pktline import RefCommand


@dataclass
class GitIntent(Intent):
    _project: str
    operation: str  # "advertise" | "upload-pack" | "receive-pack"
    # Audit-facing verb, not always an HTTP method: advertise→"GET",
    # upload-pack→"POST", receive-pack→"push" (kept for JSONL compatibility).
    _method: str
    # Set by the guard's parse(), never derived here: True for receive-pack
    # and for push discovery (advertise with ?service=git-receive-pack) — the
    # write token it needs must never reach upstream in read-only/off mode.
    _writes: bool = False
    service: str = "git-upload-pack"  # advertise only
    ref_commands: list[RefCommand] = field(default_factory=list)  # receive-pack only
    # Plumbing `forward` needs to stream the *unchanged* body upstream
    # (SHA-preserving, W7.3) — not decision-relevant, just carried along.
    head: bytes = b""
    rest: Optional[AsyncIterator[bytes]] = None
    content_type: str = "application/x-git-receive-pack-request"
    extra_headers: dict[str, str] = field(default_factory=dict)
    sideband: bool = False

    @property
    def writes(self) -> bool:
        return self._writes

    @property
    def project(self) -> str:
        return self._project

    @property
    def method(self) -> str:
        return self._method
