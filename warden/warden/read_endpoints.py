"""Data-driven REST read-endpoint table (B1, §02-befunde.md, §06-migration.md
Schritt 1): the security line is **content, not visibility**.

Project-bound paths (``/projects/{id}/…``) are gated by the project allowlist
(R6) exactly as before — this table plays no role there, ``policy._decide_api``
never even calls it in that case. This table governs *projectless* GET/HEAD/
OPTIONS paths, where there is no project id in the path for R6 to gate:

* Project and group names/metadata may be read freely (R1) — the documented
  discovery flow (``GET /groups/<id>/projects`` in ``AGENT.md``) stays
  unrestricted.
* Repository *content* — blobs, commits, wiki, snippets, diffs — must never
  leak across the ``allowed_projects`` boundary through a projectless
  endpoint, so those are denied (R6), even though the token can technically
  see them.
* Anything not in the table is default-denied (A1) — a new metadata endpoint
  is a deliberate table edit, never an accidental pass-through.

This is a *minimal* table (the four categories from B1); a later migration
step (§06 Schritt 4, the endpoint catalogue) only extends it, never replaces
it.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Callable, Optional

from .model import Decision, ProxyRequest, TokenKind
from .path_template import compile_template

# A read-table row decides outright — unlike api_endpoints.Check (None ⇒
# "still passing"), a read row must always return the terminal Decision: the
# *same* path template (``/search``) can be R1 or R6 depending on the `scope`
# query field, so there is no shared "denied unless proven otherwise" default
# to fall back on within a single row.
ReadCheck = Callable[[ProxyRequest], Decision]

# Search `scope` values that only return metadata (project/issue/MR/user
# listings) — safe to pass through projectless (R1). Everything else,
# including a missing or unrecognised scope, is denied: this is an allowlist
# of known-safe scopes, not a blocklist of known-dangerous ones (A1/A8), so a
# future GitLab scope the warden has never heard of fails closed.
_METADATA_SEARCH_SCOPES = frozenset({"projects", "issues", "merge_requests", "milestones", "users"})


def _allow_metadata(req: ProxyRequest) -> Decision:
    """Category 2 (B1): projectless names/metadata — always readable."""
    return Decision(True, "R1", "read pass-through (projectless metadata)", TokenKind.READ)


def _deny_snippets(req: ProxyRequest) -> Decision:
    """Category 3 (B1): snippets are repository content with no project scope."""
    return Decision(
        False, "R6", f"projectless snippet content is not permitted: {req.method} {req.path}"
    )


def _search_scope_gate(req: ProxyRequest) -> Decision:
    """Category 3 (B1): global/group search — the `scope` query field decides.

    F12: ``scope`` lives in ``req.fields`` (query params are folded in by
    ``api_proxy._extract_fields`` for every method, including GET) and the raw
    query string is forwarded unchanged by ``api_proxy._forward`` — so this
    check and the upstream request see the same value.
    """
    scope = req.fields.get("scope")
    if scope in _METADATA_SEARCH_SCOPES:
        return Decision(True, "R1", f"read pass-through (search scope={scope!r})", TokenKind.READ)
    return Decision(
        False,
        "R6",
        f"projectless search with scope {scope!r} may return repository content: "
        f"{req.method} {req.path}",
    )


@dataclass(frozen=True)
class ReadEndpoint:
    template: str  # e.g. "/groups/{id}/projects"
    decide: ReadCheck  # returns the terminal Decision for a path match

    @functools.cached_property
    def regex(self) -> re.Pattern[str]:
        return compile_template(self.template)


READ_ENDPOINTS: tuple[ReadEndpoint, ...] = (
    # --- category 3 (B1): projectless, content-capable → deny. Listed first —
    # none of these templates overlap a category-2 row below, but keeping the
    # denies up front mirrors WRITE_ENDPOINTS' "most specific / most sensitive
    # first" convention. ---
    ReadEndpoint("/snippets", _deny_snippets),
    ReadEndpoint("/snippets/{rest}", _deny_snippets),
    ReadEndpoint("/search", _search_scope_gate),
    ReadEndpoint("/groups/{id}/search", _search_scope_gate),
    # --- category 2 (B1): projectless metadata → allow (R1). AGENT.md's
    # documented discovery flow (`GET /groups/<id>/projects`) lives here. ---
    ReadEndpoint("/projects", _allow_metadata),
    ReadEndpoint("/users", _allow_metadata),
    ReadEndpoint("/users/{id}", _allow_metadata),
    ReadEndpoint("/user", _allow_metadata),
    ReadEndpoint("/user/{rest}", _allow_metadata),
    ReadEndpoint("/version", _allow_metadata),
    ReadEndpoint("/metadata", _allow_metadata),
    ReadEndpoint("/groups", _allow_metadata),
    ReadEndpoint("/groups/{id}", _allow_metadata),
    ReadEndpoint("/groups/{id}/projects", _allow_metadata),
    ReadEndpoint("/groups/{id}/subgroups", _allow_metadata),
    ReadEndpoint("/groups/{id}/descendant_groups", _allow_metadata),
    ReadEndpoint("/merge_requests", _allow_metadata),
    ReadEndpoint("/issues", _allow_metadata),
    ReadEndpoint("/events", _allow_metadata),
    ReadEndpoint("/broadcast_messages", _allow_metadata),
)


def match_read(path: str) -> Optional[ReadEndpoint]:
    """Return the matching read-table row, or ``None`` (→ category 4, default-deny)."""
    path = path.rstrip("/")
    for ep in READ_ENDPOINTS:
        if ep.regex.fullmatch(path):
            return ep
    return None
