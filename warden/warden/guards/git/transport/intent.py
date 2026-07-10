"""The git guard's Intent: forge-agnostic, covers all three git Smart-HTTP
operations, dispatched on operation.

needs_write is False except for receive-pack and push discovery — it's
about the credential, not about changing state."""

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
    # Raw Host header, read by the guard's parse(); host_gate checks it
    # against Config.host_allowed. The guard resolves the canonical host.
    _host: str = ""
    # Set by the guard's parse(): True for receive-pack and push discovery —
    # the write token it needs must never reach upstream on a read-only host.
    _needs_write: bool = False
    service: str = "git-upload-pack"  # advertise only
    ref_commands: list[RefCommand] = field(default_factory=list)  # receive-pack only
    # Plumbing `forward` needs to stream the *unchanged* body upstream
    # (SHA-preserving) — not decision-relevant, just carried along.
    head: bytes = b""
    rest: Optional[AsyncIterator[bytes]] = None
    content_type: str = "application/x-git-receive-pack-request"
    extra_headers: dict[str, str] = field(default_factory=dict)
    sideband: bool = False
    # receive-pack only: Content-Length when sent; None when absent (e.g.
    # chunked) — the size gate then has nothing to check and lets the push through.
    push_bytes: Optional[int] = None

    @property
    def needs_write(self) -> bool:
        return self._needs_write

    @property
    def project(self) -> str:
        return self._project

    @property
    def method(self) -> str:
        return self._method

    @property
    def host(self) -> str:
        return self._host
