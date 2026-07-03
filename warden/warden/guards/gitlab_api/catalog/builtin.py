"""The merge endpoint: a built-in deny invariant, not a catalog row (§04.2,
§04.3; docs/design/architecture-generalization/04-policy-erweiterbarkeit.md
§04.2: "Die Merge-Zeile … ist kein aktivierbarer Eintrag, sondern eine
eingebaute Deny-Invariante").

``PUT /projects/{id}/merge_requests/{iid}/merge`` must deny regardless of
which catalog entries a deployment activates — including a hypothetical
``enable = []`` that turns off every optional entry. Putting it in
``entries.CATALOG`` would make it *just another row*, activatable and
therefore (by construction) also deactivatable; keeping it here means
``guards.gitlab_api.policy.capability_gate`` checks it before ever consulting
the effective table.

The FORBIDDEN capability layer (``core.capabilities``) is a second,
independent reason this endpoint can never be allowed even if this match were
somehow bypassed (defense-in-depth, A10) — see ``test_capabilities.py``'s
proof that a hypothetical catalog row shaped like this one is still denied by
the capability layer alone.
"""

from __future__ import annotations

from ....core.path_template import compile_template
from .model import PROBE_PROJECT_PATH, DenyProbe

MERGE_METHOD = "PUT"
MERGE_TEMPLATE = "/projects/{id}/merge_requests/{iid}/merge"
_MERGE_REGEX = compile_template(MERGE_TEMPLATE)


def is_builtin_merge_endpoint(method: str, path: str) -> bool:
    """True iff ``method``/``path`` is the built-in merge invariant's shape."""
    return method.upper() == MERGE_METHOD and bool(_MERGE_REGEX.fullmatch(path.rstrip("/")))


# Global deny-probes (§04.4) for the built-in invariants — run by the
# startgate unconditionally, independent of which catalog entries are
# activated (unlike a catalog entry's own ``deny_probes``, which only run
# when that entry is enabled).
BUILTIN_DENY_PROBES: tuple[DenyProbe, ...] = (
    DenyProbe(
        description="the merge endpoint is a built-in invariant, never activatable",
        method=MERGE_METHOD,
        path=f"/projects/{PROBE_PROJECT_PATH}/merge_requests/7/merge",
    ),
    DenyProbe(
        # Denied even if a deployment disabled mr.update entirely: either the
        # entry is active and the FORBIDDEN capability layer catches the
        # alias, or it is inactive and the request is default-denied (R3) for
        # having no matching endpoint at all — either way, never allowed.
        description="state_event=merge is a merge alias, denied regardless of mr.update activation",
        method="PUT",
        path=f"/projects/{PROBE_PROJECT_PATH}/merge_requests/7",
        fields={"state_event": "merge"},
        mr_owner_ok=True,
    ),
)
