"""The git guard's Intent (§03.3, F3): replaces the old channel-union
``ProxyRequest`` for git pushes. Forge-agnostic — the only non-primitive field
is a git-protocol concept (``RefCommand``), never a forge one.

Every :class:`GitPushIntent` the kernel ever sees comes from a
``git-receive-pack`` request — the discovery/fetch routes (``advertise``,
``upload_pack``) are reads that never carry ref commands and stay outside the
kernel pipeline entirely (§03.2's "dünne Handler" carve-out), so ``writes`` is
unconditionally ``True`` here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from .pktline import RefCommand


@dataclass
class GitPushIntent:
    project: str
    ref_commands: list[RefCommand] = field(default_factory=list)
    # Plumbing `forward` needs to stream the *unchanged* body upstream
    # (SHA-preserving, W7.3) — not decision-relevant, just carried along.
    head: bytes = b""
    rest: Optional[AsyncIterator[bytes]] = None
    content_type: str = "application/x-git-receive-pack-request"
    extra_headers: dict[str, str] = field(default_factory=dict)
    sideband: bool = False
    # Audit-facing verb (§03.2 core.model.Intent) — not an HTTP method; kept
    # as the pre-Schritt-5 literal ("push") for byte-compatible JSONL.
    method: str = "push"

    @property
    def writes(self) -> bool:
        # The only intent this guard's kernel pipeline ever parses is a
        # receive-pack push — always a write by construction.
        return True
