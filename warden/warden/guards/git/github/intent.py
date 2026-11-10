"""GitHub REST guard intent: the parsed, decision-relevant shape of one request."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ....core.model import Intent

_READ_METHODS = ("GET", "HEAD", "OPTIONS")


class IssueKind(str, Enum):
    """What an issue number names: every pull request is also an issue."""

    PULL = "pull"
    ISSUE = "issue"
    UNKNOWN = "unknown"


@dataclass
class GithubIntent(Intent):
    """One /api/v3 request. body_ok is False when the body is not a single
    JSON object of known-typed fields — GitHub reads every write body as
    JSON whatever its Content-Type says, so anything else is undecidable."""

    _project: str
    _method: str
    _host: str = ""

    path: str = ""  # REST path after /api/v3, query-less
    fields: dict[str, str] = field(default_factory=dict)
    body_keys: frozenset[str] = frozenset()
    body_ok: bool = True
    number: Optional[int] = None  # pulls/{n} or issues/{n}
    run_id: Optional[int] = None  # actions/runs/{id}
    job_id: Optional[int] = None  # actions/jobs/{id}
    # Resolved before recognition for issue-number rows; UNKNOWN denies.
    issue_kind: IssueKind = IssueKind.UNKNOWN
    # Resolved by enrich via an upstream lookup; None ⇒ unverifiable.
    scope_ok: Optional[bool] = None
    body: bytes = b""
    raw_query: str = ""

    @property
    def is_graphql(self) -> bool:
        return self.path == "/graphql" or self.path.startswith("/graphql/")

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

    @property
    def repo_owner(self) -> str:
        return self._project.split("/", 1)[0]
