"""Data-driven REST read-endpoint table: security line is **content, not visibility**.

Project-bound paths are gated by the project allowlist (R6); this table governs
*projectless* GET/HEAD/OPTIONS paths:

* Project/group metadata (R1) — documented discovery flow stays unrestricted.
* Repository content — blobs, commits, wiki — denied (R6) to prevent boundary leaks.
* Anything not in table is default-denied — new metadata endpoints are deliberate edits.

The endpoint catalog only extends this, never replaces it.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Callable, Optional

from ...core.model import Decision, TokenKind
from ...core.path_template import compile_template
from ...core.rules import R1, R6
from .intent import ApiIntent

# A read-table row decides outright — unlike a write-endpoint's Check (None ⇒
# "still passing"), a read row must always return the terminal Decision: the
# *same* path template (``/search``) can be R1 or R6 depending on the `scope`
# query field, so there is no shared "denied unless proven otherwise" default
# to fall back on within a single row.
ReadCheck = Callable[[ApiIntent], Decision]

# Search `scope` values that only return metadata (project/issue/MR/user
# listings) — safe to pass through projectless (R1). Everything else,
# including a missing or unrecognised scope, is denied: this is an allowlist
# of known-safe scopes, not a blocklist of known-dangerous ones (A1/A8), so a
# future GitLab scope the warden has never heard of fails closed.
_METADATA_SEARCH_SCOPES = frozenset({"projects", "issues", "merge_requests", "milestones", "users"})


def _allow_metadata(intent: ApiIntent) -> Decision:
    """Projectless names/metadata — always readable."""
    return Decision(True, R1, "read pass-through (projectless metadata)", TokenKind.READ)


def _deny_snippets(intent: ApiIntent) -> Decision:
    """Snippets are repository content with no project scope."""
    return Decision(
        False, R6, f"projectless snippet content is not permitted: {intent.method} {intent.path}"
    )


def _search_scope_gate(intent: ApiIntent) -> Decision:
    """Global/group search — the `scope` query field decides.

    ``scope`` lives in ``intent.fields`` (query params are folded in by
    the guard's ``parse``/``extract_fields`` for every method, including GET)
    and the raw query string is forwarded unchanged — so this check and the
    upstream request see the same value.
    """
    scope = intent.fields.get("scope")
    if scope in _METADATA_SEARCH_SCOPES:
        return Decision(True, R1, f"read pass-through (search scope={scope!r})", TokenKind.READ)
    return Decision(
        False,
        R6,
        f"projectless search with scope {scope!r} may return repository content: "
        f"{intent.method} {intent.path}",
    )


@dataclass(frozen=True)
class ReadEndpoint:
    template: str  # e.g. "/groups/{id}/projects"
    decide: ReadCheck  # returns the terminal Decision for a path match

    @functools.cached_property
    def regex(self) -> re.Pattern[str]:
        return compile_template(self.template)


READ_ENDPOINTS: tuple[ReadEndpoint, ...] = (
    # --- Content-capable denies: listed first by convention (most specific/sensitive first) ---
    ReadEndpoint("/snippets", _deny_snippets),
    ReadEndpoint("/snippets/{rest}", _deny_snippets),
    ReadEndpoint("/search", _search_scope_gate),
    ReadEndpoint("/groups/{id}/search", _search_scope_gate),
    # --- Projectless metadata allow (R1): documented discovery flow ---
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
