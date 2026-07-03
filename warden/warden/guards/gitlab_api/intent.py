"""The REST guard's Intent (§03.3, F3): replaces the old channel-union
``ProxyRequest`` for GitLab REST requests. ``guards.git.intent`` is git's own,
unrelated counterpart — the two guards no longer share one type that had to
carry both shapes at once (F3's actual complaint about ``ProxyRequest``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from warden.core.model import Intent

if TYPE_CHECKING:  # only for the annotation; avoids a load cycle (catalog imports this module)
    from .catalog.model import CatalogEntry

_READ_METHODS = ("GET", "HEAD", "OPTIONS")


@dataclass
class ApiIntent(Intent):
    """The parsed, decision-relevant shape of one REST request (§6.9)."""

    _project: str
    _method: str

    path: str = ""  # REST path after /api/v4, e.g. /projects/123/merge_requests
    endpoint: Optional["CatalogEntry"] = None  # matched catalog entry (writes only)
    fields: dict[str, Any] = field(default_factory=dict)  # extracted body/query fields
    # Resolved by the guard's enrich() via an upstream lookup (W6.2); None ⇒ unverifiable.
    mr_owner_ok: Optional[bool] = None
    iid: Optional[int] = None  # merge_requests/{iid} from the path, if present
    body: bytes = b""  # raw request body, carried for forward() (F12: matching stays query-less)
    raw_query: str = ""  # exact wire query string, reattached only at forward() (F12)

    @property
    def writes(self) -> bool:
        # §03.2: derived by the parser, never by a Decision.
        return self.method.upper() not in _READ_METHODS

    @property
    def project(self) -> str:
        return self._project

    @property
    def method(self) -> str:
        return self._method


@dataclass
class GraphqlIntent(Intent):
    """`/api/graphql` — always denied; ``project``/``writes`` carry no weight."""

    path: str
    _method: str

    @property
    def writes(self) -> bool:
        return False

    @property
    def project(self) -> str:
        return ""

    @property
    def method(self) -> str:
        return self._method
