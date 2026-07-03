"""The merge endpoint: a built-in deny invariant, not a catalog row.

``PUT /projects/{id}/merge_requests/{iid}/merge`` must deny regardless of
which catalog entries a deployment activates — including a hypothetical
``enable = []`` that turns off every optional entry. Putting it in
``entries.CATALOG`` would make it *just another row*, activatable and
therefore also deactivatable; keeping it here means
``guards.gitlab_api.policy.capability_gate`` checks it before ever consulting
the effective table.

The FORBIDDEN capability layer (``core.capabilities``) is a second,
independent reason this endpoint can never be allowed even if this match were
somehow bypassed — defense-in-depth.
"""

from __future__ import annotations

from ....core.path_template import compile_template

MERGE_METHOD = "PUT"
MERGE_TEMPLATE = "/projects/{id}/merge_requests/{iid}/merge"
_MERGE_REGEX = compile_template(MERGE_TEMPLATE)


def is_builtin_merge_endpoint(method: str, path: str) -> bool:
    """True iff ``method``/``path`` is the built-in merge invariant's shape."""
    return method.upper() == MERGE_METHOD and bool(_MERGE_REGEX.fullmatch(path.rstrip("/")))
