"""Data-driven write-endpoint allowlist (W6.1, §6.9).

The few permitted REST write endpoints are a *table*, not code branches.
Adapting to a GitLab v4 change is a config edit + test, never a logic rewrite.
Anything without a match is default-denied and audited (§6.9: "ageing safely").
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WriteEndpoint:
    method: str
    template: str  # e.g. "/projects/{id}/merge_requests"
    checks: tuple[str, ...]  # pure check names evaluated in policy.decide
    rule: str  # R-id for the audit log
    kind: str  # 'mr' | 'note' | 'pipeline' | 'merge' | ... — for quota accounting

    @functools.cached_property
    def regex(self) -> re.Pattern[str]:
        # {id}/{iid} → one non-slash, URL-encoded path segment.
        segments = []
        for seg in self.template.split("/"):
            if seg.startswith("{") and seg.endswith("}"):
                segments.append("[^/]+")
            else:
                segments.append(re.escape(seg))
        return re.compile("/".join(segments))


# Check names (resolved in policy.py):
#   src_branch_prefix   source_branch starts with BRANCH_PREFIX (R2/R3)
#   mr_owned_by_claude  target MR is prefixed AND authored by the service account
#   not_merge_intent    body has no state_event=merge (R4 alias guard)
#   ref_prefix          pipeline ref starts with BRANCH_PREFIX (R3)
#   ALWAYS_DENY         unconditional 403 (R4)
WRITE_ENDPOINTS: tuple[WriteEndpoint, ...] = (
    # Merge — ALWAYS forbidden (R4). Listed first so it can never be shadowed.
    WriteEndpoint(
        "PUT", "/projects/{id}/merge_requests/{iid}/merge", ("ALWAYS_DENY",), "R4", "merge"
    ),
    # Create MR — only if source_branch carries the prefix (R2/R3).
    WriteEndpoint(
        "POST", "/projects/{id}/merge_requests", ("src_branch_prefix",), "R3", "mr"
    ),
    # Note/comment — only on an MR owned by Claude.
    WriteEndpoint(
        "POST",
        "/projects/{id}/merge_requests/{iid}/notes",
        ("mr_owned_by_claude",),
        "R3",
        "note",
    ),
    # Edit MR (incl. close) — same ownership, and never a merge intent.
    WriteEndpoint(
        "PUT",
        "/projects/{id}/merge_requests/{iid}",
        ("mr_owned_by_claude", "not_merge_intent"),
        "R3",
        "mr_update",
    ),
    # Trigger CI — pipeline on a claude/* ref.
    WriteEndpoint(
        "POST", "/projects/{id}/pipeline", ("ref_prefix",), "R3", "pipeline"
    ),
)


def match_endpoint(method: str, path: str) -> WriteEndpoint | None:
    """Return the matching write endpoint, or None (→ default-deny)."""
    path = path.rstrip("/")
    for ep in WRITE_ENDPOINTS:
        if ep.method == method.upper() and ep.regex.fullmatch(path):
            return ep
    return None
