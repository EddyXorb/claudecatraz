"""Data-driven REST read-endpoint table: security line is **content, not visibility**.

Project-bound paths are gated by the project allowlist (R6); this table governs
*projectless* GET/HEAD/OPTIONS paths:

* Project/group metadata (R1) — documented discovery flow stays unrestricted.
* Repository content — blobs, commits, wiki — denied (R6) to prevent boundary leaks.
* Anything not in table is default-denied — new metadata endpoints are deliberate edits.

The endpoint catalog only extends this, never replaces it.

Every row is a :class:`~.catalog.model.Recognizer` with
``scope_kind=ScopeKind.CONTENT_EXPOSURE` (§07 Punkt 7) — the same type the
write catalog uses. Unlike a write recognizer's plain ``namespace_field``
data, content-exposure genuinely needs a per-row classifier (``classify``):
the *same* path template (``/search``) is metadata or content depending on
the request's ``scope`` query field, so there is no static "denied unless
proven otherwise" default a data field alone could express. The classifier
is narrowly typed — it returns only a closed :class:`~.catalog.model.ReadClass`
plus a reason string, never an arbitrary Decision — so the one generic
``policy.decide_scope`` still owns turning it into a terminal Decision.
"""

from __future__ import annotations

from typing import Optional

from ..intent import ApiIntent
from .model import ReadClass, Recognizer, ScopeKind

# Search `scope` values that only return metadata (project/issue/MR/user
# listings) — safe to pass through projectless (R1). Everything else,
# including a missing or unrecognised scope, is denied: this is an allowlist
# of known-safe scopes, not a blocklist of known-dangerous ones (A1/A8), so a
# future GitLab scope the warden has never heard of fails closed.
_METADATA_SEARCH_SCOPES = frozenset({"projects", "issues", "merge_requests", "milestones", "users"})


def _allow_metadata(intent: ApiIntent) -> tuple[ReadClass, str]:
    """Projectless names/metadata — always readable."""
    return ReadClass.METADATA, "read pass-through (projectless metadata)"


def _deny_snippets(intent: ApiIntent) -> tuple[ReadClass, str]:
    """Snippets are repository content with no project scope."""
    return (
        ReadClass.CONTENT,
        f"projectless snippet content is not permitted: {intent.method} {intent.path}",
    )


def _search_scope_gate(intent: ApiIntent) -> tuple[ReadClass, str]:
    """Global/group search — the `scope` query field decides.

    ``scope`` lives in ``intent.fields`` (query params are folded in by
    the guard's ``parse``/``extract_fields`` for every method, including GET)
    and the raw query string is forwarded unchanged — so this check and the
    upstream request see the same value.
    """
    scope = intent.fields.get("scope")
    if scope in _METADATA_SEARCH_SCOPES:
        return ReadClass.METADATA, f"read pass-through (search scope={scope!r})"
    return (
        ReadClass.CONTENT,
        f"projectless search with scope {scope!r} may return repository content: "
        f"{intent.method} {intent.path}",
    )


READ_ENDPOINTS: tuple[Recognizer, ...] = (
    # --- Content-capable denies: listed first by convention (most specific/sensitive first) ---
    Recognizer(
        id="read.snippets",
        method="GET",
        template="/snippets",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_deny_snippets,
    ),
    Recognizer(
        id="read.snippets_rest",
        method="GET",
        template="/snippets/{rest}",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_deny_snippets,
    ),
    Recognizer(
        id="read.search",
        method="GET",
        template="/search",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_search_scope_gate,
    ),
    Recognizer(
        id="read.group_search",
        method="GET",
        template="/groups/{id}/search",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_search_scope_gate,
    ),
    # --- Projectless metadata allow (R1): documented discovery flow ---
    Recognizer(
        id="read.projects",
        method="GET",
        template="/projects",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.users",
        method="GET",
        template="/users",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.user_by_id",
        method="GET",
        template="/users/{id}",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.user",
        method="GET",
        template="/user",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.user_rest",
        method="GET",
        template="/user/{rest}",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.version",
        method="GET",
        template="/version",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.metadata",
        method="GET",
        template="/metadata",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.groups",
        method="GET",
        template="/groups",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.group_by_id",
        method="GET",
        template="/groups/{id}",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.group_projects",
        method="GET",
        template="/groups/{id}/projects",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.group_subgroups",
        method="GET",
        template="/groups/{id}/subgroups",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.group_descendant_groups",
        method="GET",
        template="/groups/{id}/descendant_groups",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.merge_requests",
        method="GET",
        template="/merge_requests",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.issues",
        method="GET",
        template="/issues",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.events",
        method="GET",
        template="/events",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
    Recognizer(
        id="read.broadcast_messages",
        method="GET",
        template="/broadcast_messages",
        scope_kind=ScopeKind.CONTENT_EXPOSURE,
        classify=_allow_metadata,
    ),
)


def match_read(path: str) -> Optional[Recognizer]:
    """Return the matching read-table row, or ``None`` (→ category 4, default-deny).

    Matches on template alone, not method: every row here is reachable only
    for GET/HEAD/OPTIONS (``policy.decide`` dispatches read methods here
    before ever consulting the write catalog), so there is no ambiguity a
    method check would resolve.
    """
    path = path.rstrip("/")
    for ep in READ_ENDPOINTS:
        if ep.regex.fullmatch(path):
            return ep
    return None
