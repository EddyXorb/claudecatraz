"""Core policy data types (W5): the pure values exchanged between the proxies,
the policy core, and the audit/state layers.

Kept in a leaf module so the endpoint table (``api_endpoints``) can hold the
check predicates directly without an import cycle back through ``policy``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

from .pktline import RefCommand

if TYPE_CHECKING:  # only for the annotation; no runtime import (avoids a cycle)
    from .api_endpoints import WriteEndpoint


class TokenKind(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    NONE = "NONE"


class Channel(str, Enum):
    """The proxy path a request arrived on."""

    API = "api"  # REST reverse-proxy (api_proxy)
    GIT = "git"  # git Smart-HTTP proxy (git_proxy)


@dataclass(frozen=True)
class Decision:
    allow: bool
    rule: str  # "R1".."R6" — for the audit log
    reason: str
    token: TokenKind = TokenKind.NONE  # which upstream token, if allow


@dataclass(frozen=True)
class StateView:
    """Snapshot of the quota counters (W5). ``locked`` ⇒ fail-safe deny (§6.11)."""

    open_mrs: int = 0
    open_branches: int = 0
    writes_last_hour: int = 0
    locked: bool = False


@dataclass
class ProxyRequest:
    """The parsed intent handed to :func:`policy.decide` — no transport state."""

    channel: Channel
    project: str
    method: str = ""
    path: str = ""  # REST path after /api/v4, e.g. /projects/123/merge_requests
    endpoint: Optional[WriteEndpoint] = None  # matched write endpoint (api)
    fields: dict = field(default_factory=dict)  # extracted body/query fields
    ref_commands: list[RefCommand] = field(default_factory=list)  # git push
    # Resolved by api_proxy via an upstream lookup (W6.2); None ⇒ unverifiable.
    mr_owner_ok: Optional[bool] = None
