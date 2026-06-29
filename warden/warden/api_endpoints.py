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
from typing import Callable, Optional

from .config import Config
from .model import Decision, ProxyRequest, StateView

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


def src_branch_prefix(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
    src = req.fields.get("source_branch", "")
    if src.startswith(cfg.branch_prefix):
        return None
    return Decision(False, "R2", f"source_branch {src!r} without prefix {cfg.branch_prefix!r}")


def ref_prefix(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
    ref = req.fields.get("ref", "")
    if ref.startswith(cfg.branch_prefix):
        return None
    return Decision(False, "R2", f"ref {ref!r} without prefix {cfg.branch_prefix!r}")


def mr_owned_by_claude(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
    if req.mr_owner_ok is True:
        return None
    if req.mr_owner_ok is None:
        return Decision(False, "R3", "MR ownership could not be verified")
    return Decision(False, "R3", "MR not owned by the service account")


def not_merge_intent(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
    if req.fields.get("state_event") == "merge":
        return Decision(False, "R4", "state_event=merge is a merge alias")
    return None


def always_deny(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
    return Decision(False, "R4", "merge is never permitted")


@dataclass(frozen=True)
class WriteEndpoint:
    method: str
    template: str  # e.g. "/projects/{id}/merge_requests"
    checks: tuple[Check, ...]  # pure predicates, all must pass (run in policy.decide)
    rule: str  # R-id for the audit log
    kind: EndpointKind  # for quota accounting

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


WRITE_ENDPOINTS: tuple[WriteEndpoint, ...] = (
    # Merge an MR into its target branch. ALWAYS forbidden (R4) — the agent may
    # never merge. Listed first so no later, looser row can ever shadow it.
    WriteEndpoint(
        "PUT", "/projects/{id}/merge_requests/{iid}/merge", (always_deny,), "R4", EndpointKind.MERGE
    ),
    # Open a new merge request. Allowed only when its source_branch carries the
    # claude/ prefix (R2/R3) — the agent can only propose its own branches.
    WriteEndpoint(
        "POST", "/projects/{id}/merge_requests", (src_branch_prefix,), "R3", EndpointKind.MR
    ),
    # Post a top-level comment ("note") on an MR. Allowed only on an MR the
    # service account authored (R3 ownership).
    WriteEndpoint(
        "POST",
        "/projects/{id}/merge_requests/{iid}/notes",
        (mr_owned_by_claude,),
        "R3",
        EndpointKind.NOTE,
    ),
    # Start a new discussion thread on an MR — including an *inline diff comment*
    # on a specific file/line (pass a `position`). This is how line-level code
    # review comments are made. Same R3 ownership as a plain note.
    WriteEndpoint(
        "POST",
        "/projects/{id}/merge_requests/{iid}/discussions",
        (mr_owned_by_claude,),
        "R3",
        EndpointKind.NOTE,
    ),
    # Reply to an existing discussion thread on an MR (add a note under a given
    # discussion_id). Lets the agent answer review threads it started. Same R3
    # ownership — the iid still identifies the owning MR.
    WriteEndpoint(
        "POST",
        "/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}/notes",
        (mr_owned_by_claude,),
        "R3",
        EndpointKind.NOTE,
    ),
    # Edit an MR — change title/description/labels, or close it (state_event).
    # Same R3 ownership, and not_merge_intent blocks state_event=merge (the R4
    # merge alias) so this row can't be used to sneak a merge through.
    WriteEndpoint(
        "PUT",
        "/projects/{id}/merge_requests/{iid}",
        (mr_owned_by_claude, not_merge_intent),
        "R3",
        EndpointKind.MR_UPDATE,
    ),
    # Trigger a CI pipeline. Allowed only for a ref carrying the claude/ prefix
    # (R3) — the agent runs CI on its own branches, not protected ones.
    WriteEndpoint(
        "POST", "/projects/{id}/pipeline", (ref_prefix,), "R3", EndpointKind.PIPELINE
    ),
)


def match_endpoint(method: str, path: str) -> Optional[WriteEndpoint]:
    """Return the matching write endpoint, or None (→ default-deny)."""
    path = path.rstrip("/")
    for ep in WRITE_ENDPOINTS:
        if ep.method == method.upper() and ep.regex.fullmatch(path):
            return ep
    return None
