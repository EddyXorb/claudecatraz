"""Deny-probes per catalog entry: must-deny examples the startgate runs
for each activated entry, indexed by entry id so the catalog table stays legible.
"""

from __future__ import annotations

from .model import OTHER_PROJECT_PATH, PROBE_PROJECT_PATH, DenyProbe

ENTRY_DENY_PROBES: dict[str, tuple[DenyProbe, ...]] = {
    "mr.create": (
        DenyProbe(
            description="source_branch outside the branch namespace is denied",
            method="POST",
            path=f"/projects/{PROBE_PROJECT_PATH}/merge_requests",
            fields={"source_branch": "main", "target_branch": "main"},
        ),
    ),
    "mr.note": (
        DenyProbe(
            description="a note on an MR whose ownership can't be verified is denied",
            method="POST",
            path=f"/projects/{PROBE_PROJECT_PATH}/merge_requests/7/notes",
            fields={"body": "hi"},
        ),
    ),
    "mr.discussion": (
        DenyProbe(
            description="a discussion on an unverifiable MR is denied",
            method="POST",
            path=f"/projects/{PROBE_PROJECT_PATH}/merge_requests/7/discussions",
            fields={"body": "nit"},
        ),
    ),
    "mr.discussion_reply": (
        DenyProbe(
            description="a discussion reply on an unverifiable MR is denied",
            method="POST",
            path=(f"/projects/{PROBE_PROJECT_PATH}/merge_requests/7/discussions/abc123/notes"),
            fields={"body": "done"},
        ),
    ),
    "mr.update": (
        DenyProbe(
            description="editing an MR whose ownership can't be verified is denied",
            method="PUT",
            path=f"/projects/{PROBE_PROJECT_PATH}/merge_requests/7",
            fields={"title": "x"},
        ),
        DenyProbe(
            description="state_event=merge is denied even on the bot's own MR",
            method="PUT",
            path=f"/projects/{PROBE_PROJECT_PATH}/merge_requests/7",
            fields={"state_event": "merge"},
            mr_owner_ok=True,
        ),
    ),
    "pipeline.trigger": (
        DenyProbe(
            description="triggering a pipeline on a protected ref is denied",
            method="POST",
            path=f"/projects/{PROBE_PROJECT_PATH}/pipeline",
            fields={"ref": "main"},
        ),
    ),
    "branch.create": (
        DenyProbe(
            description="creating a branch outside the namespace via REST is denied",
            method="POST",
            path=f"/projects/{PROBE_PROJECT_PATH}/repository/branches",
            fields={"branch": "main", "ref": "main"},
        ),
    ),
    "issue.create": (
        DenyProbe(
            # No entry-specific check exists to probe (checks=() by
            # design) — this instead pins down the invariant every entry
            # shares regardless of its own checks: the project boundary
            # (R6) still applies. A future change that special-cased some
            # catalog entries to skip project_gate would fail this.
            description="the project boundary still applies with no entry-specific checks",
            method="POST",
            path=f"/projects/{OTHER_PROJECT_PATH}/issues",
            fields={"title": "x"},
        ),
    ),
}
