"""Data-driven REST write-endpoint table (W6.1, §6.9).

The few permitted GitLab REST *write* endpoints are a *table*, not code
branches — each row pairs a method+path template with the checks it must pass.
Adapting to a GitLab v4 change is a config edit + test, never a logic rewrite.
Anything without a match is default-denied and audited (§6.9: "ageing safely").

This is the API write path only; reads (R1), the git push path (R2), and the
project boundary (R6) live elsewhere. The check predicates live here, beside
the table that references them, so the table can hold the callables directly.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Optional

from .capabilities import Capability
from .config import Config
from .model import Decision, ProxyRequest, StateView
from .path_template import compile_template
from .rules import R2, R3, R4

# A check inspects an already-parsed request and returns a deny Decision, or
# None if the request passes (same shape as policy.project_gate / quota checks).
Check = Callable[[ProxyRequest, StateView, Config], Optional[Decision]]


class EndpointKind(str, Enum):
    """What a write endpoint creates/touches — drives quota accounting (R5)."""

    MERGE = "merge"
    MR = "mr"
    NOTE = "note"
    MR_UPDATE = "mr_update"
    PIPELINE = "pipeline"


# --- pure check predicates (W6) -------------------------------------------------


def field_has_prefix(field: str) -> Check:
    """Factory: deny unless ``req.fields[field]`` is in the branch namespace (R2).

    ``source_branch`` (MR creation) and ``ref`` (pipeline trigger) are the same
    namespace check under a different field name — one parametrised predicate
    instead of two near-identical functions. The namespace test itself lives
    in :meth:`Config.in_branch_namespace`, the single source of truth for the
    ``branch_prefixes`` union — never a direct ``startswith`` here.
    """

    def check(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
        value = req.fields.get(field, "")
        if cfg.in_branch_namespace(value):
            return None
        return Decision(
            False, R2, f"{field} {value!r} outside allowed prefixes {cfg.branch_prefixes!r}"
        )

    return check


def mr_owned_by_claude(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
    if req.mr_owner_ok is True:
        return None
    if req.mr_owner_ok is None:
        return Decision(False, R3, "MR ownership could not be verified")
    return Decision(False, R3, "MR not owned by the service account")


def not_merge_intent(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
    if req.fields.get("state_event") == "merge":
        return Decision(False, R4, "state_event=merge is a merge alias")
    return None


def always_deny(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
    return Decision(False, R4, "merge is never permitted")


@dataclass(frozen=True)
class WriteEndpoint:
    method: str
    template: str  # e.g. "/projects/{id}/merge_requests"
    checks: tuple[Check, ...]  # pure predicates, all must pass (run in policy.decide)
    rule: str  # R-id for the audit log
    kind: EndpointKind  # for quota accounting
    # Static capabilities (§03.4, B2): declared by the table author in code,
    # never by the end user (§06.2) — see the table below for the reasoning
    # behind each row's set. Combined with field-dependent capabilities by
    # :func:`api_capabilities` before the FORBIDDEN check runs.
    capabilities: frozenset[Capability] = frozenset()

    @functools.cached_property
    def regex(self) -> re.Pattern[str]:
        # {id}/{iid} → one non-slash, URL-encoded path segment (path_template).
        return compile_template(self.template)


def api_capabilities(ep: WriteEndpoint, fields: Mapping[str, object]) -> frozenset[Capability]:
    """Endpoint capabilities plus the one field-dependent addition (§03.4, B2).

    Every row's capabilities are static — declared on the table, independent
    of the request — with one exception: ``PUT .../merge_requests/{iid}``
    only merges when the caller also sets ``state_event=merge`` (the same
    alias :func:`not_merge_intent` already guards against, W6.2). A static
    ``{merges}`` on that row would forbid the row outright (it is also the
    only way to edit an MR's title/description); a static empty set would
    miss the alias entirely. Deriving the addition from the field actually
    present, not just which row matched, is what makes the FORBIDDEN check
    airtight across the alias instead of relying solely on
    ``not_merge_intent`` staying wired to that one row.
    """
    caps = set(ep.capabilities)
    if fields.get("state_event") == "merge":
        caps.add(Capability.MERGES)
    return frozenset(caps)


WRITE_ENDPOINTS: tuple[WriteEndpoint, ...] = (
    # Merge an MR into its target branch. ALWAYS forbidden (R4) — the agent may
    # never merge. Listed first so no later, looser row can ever shadow it.
    # capabilities={merges}: this is *the* row the FORBIDDEN set exists for —
    # a Capability-layer bypass of ``always_deny`` would still be caught here.
    WriteEndpoint(
        "PUT",
        "/projects/{id}/merge_requests/{iid}/merge",
        (always_deny,),
        R4,
        EndpointKind.MERGE,
        frozenset({Capability.MERGES}),
    ),
    # Open a new merge request. Allowed only when its source_branch carries the
    # claude/ prefix (R2/R3) — the agent can only propose its own branches.
    # capabilities=∅: creating an MR touches no git ref (the branch it points
    # at was already pushed separately) — honestly not `creates_ref`.
    WriteEndpoint(
        "POST",
        "/projects/{id}/merge_requests",
        (field_has_prefix("source_branch"),),
        R3,
        EndpointKind.MR,
    ),
    # Post a top-level comment ("note") on an MR. Allowed only on an MR the
    # service account authored (R3 ownership). capabilities=∅: a comment.
    WriteEndpoint(
        "POST",
        "/projects/{id}/merge_requests/{iid}/notes",
        (mr_owned_by_claude,),
        R3,
        EndpointKind.NOTE,
    ),
    # Start a new discussion thread on an MR — including an *inline diff comment*
    # on a specific file/line (pass a `position`). This is how line-level code
    # review comments are made. Same R3 ownership as a plain note.
    # capabilities=∅: a comment.
    WriteEndpoint(
        "POST",
        "/projects/{id}/merge_requests/{iid}/discussions",
        (mr_owned_by_claude,),
        R3,
        EndpointKind.NOTE,
    ),
    # Reply to an existing discussion thread on an MR (add a note under a given
    # discussion_id). Lets the agent answer review threads it started. Same R3
    # ownership — the iid still identifies the owning MR. capabilities=∅.
    WriteEndpoint(
        "POST",
        "/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}/notes",
        (mr_owned_by_claude,),
        R3,
        EndpointKind.NOTE,
    ),
    # Edit an MR — change title/description/labels, or close it (state_event).
    # Same R3 ownership, and not_merge_intent blocks state_event=merge (the R4
    # merge alias) so this row can't be used to sneak a merge through.
    # capabilities=∅ *statically* — the state_event=merge alias is
    # field-dependent, added by :func:`api_capabilities`, not declared here
    # (a static {merges} would forbid the row's entire, otherwise-legitimate
    # purpose of editing title/description).
    WriteEndpoint(
        "PUT",
        "/projects/{id}/merge_requests/{iid}",
        (mr_owned_by_claude, not_merge_intent),
        R3,
        EndpointKind.MR_UPDATE,
    ),
    # Trigger a CI pipeline. Allowed only for a ref carrying the claude/ prefix
    # (R3) — the agent runs CI on its own branches, not protected ones.
    # capabilities=∅: running a pipeline creates no ref and merges nothing.
    WriteEndpoint(
        "POST",
        "/projects/{id}/pipeline",
        (field_has_prefix("ref"),),
        R3,
        EndpointKind.PIPELINE,
    ),
)


def match_endpoint(method: str, path: str) -> Optional[WriteEndpoint]:
    """Return the matching write endpoint, or None (→ default-deny)."""
    path = path.rstrip("/")
    for ep in WRITE_ENDPOINTS:
        if ep.method == method.upper() and ep.regex.fullmatch(path):
            return ep
    return None
