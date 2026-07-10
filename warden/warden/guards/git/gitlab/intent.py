"""GitLab REST guard intent: the parsed, decision-relevant shape of one request.

A /api/graphql* request produces this same shape; is_graphql marks it so
policy can deny it with one explicit reason; guards.git.intent is the
transport guard's separate, unrelated counterpart."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ....core.model import Intent

_READ_METHODS = ("GET", "HEAD", "OPTIONS")
_GRAPHQL_PREFIX = "/api/graphql"


@dataclass
class ApiIntent(Intent):
    """The parsed, decision-relevant shape of one REST (or GraphQL) request."""

    _project: str
    _method: str
    # Raw Host header; host_gate checks it against Config.host_allowed, the
    # guard resolves canonical host via Config.resolve_target_host(_host).
    _host: str = ""

    path: str = ""  # REST path after /api/v4 — unstripped for /api/graphql*
    fields: dict[str, Any] = field(default_factory=dict)  # extracted body/query fields
    # Resolved by the guard's enrich() via an upstream lookup; None ⇒ unverifiable.
    mr_source_ok: Optional[bool] = None
    iid: Optional[int] = None  # merge_requests/{iid} from the path, if present
    body: bytes = b""  # raw request body, carried for forward()
    raw_query: str = ""  # exact wire query string, reattached only at forward()

    @property
    def is_graphql(self) -> bool:
        return self.path.startswith(_GRAPHQL_PREFIX)

    @property
    def needs_write(self) -> bool:
        if self.is_graphql:
            return False  # never forwarded, regardless of HTTP method
        return self.method.upper() not in _READ_METHODS

    @property
    def project(self) -> str:
        return self._project

    @property
    def method(self) -> str:
        return self._method

    @property
    def host(self) -> str:
        return self._host
