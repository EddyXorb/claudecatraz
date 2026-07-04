"""The endpoint catalog: every GitLab REST write endpoint the warden knows how to guard.

Code defines the catalog; config activates entries. ``DEFAULT_ENABLED`` is the
default set — a deployment with no ``[api.endpoints]`` section gets precisely this.
Extra entries (``branch.create``, ``issue.create``) are catalogued and tested but
only reachable when explicitly enabled via ``warden.toml``.

The merge endpoint is deliberately **not** here — it is a built-in deny invariant
(``builtin.py``), not an activatable row.

Every row is a :class:`~.model.Recognizer` (§07 Punkt 7): its ``scope_kind`` is
either ``BRANCH_NAMESPACE`` (a branch name — literal or resolved via an iid → MR
lookup — must lie in the namespace) or ``QUOTA_BY_KIND`` (project boundary +
quota only, e.g. ``issue.create``). The former author-based ``mr-ownership``
scope no longer exists (§07 Punkt 4): MR access is namespace-only, regardless
of who opened the MR.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

from ....core.capabilities import Capability
from ....core.rules import R3
from .model import EndpointKind, FieldSpec, Location, Recognizer, ScopeKind

WRITE_ENDPOINTS: tuple[Recognizer, ...] = (
    # --- default set -----
    Recognizer(
        id="mr.create",
        method="POST",
        template="/projects/{id}/merge_requests",
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
        namespace_field="source_branch",
        rule=R3,
        kind=EndpointKind.MR,
        # capabilities=∅: creating an MR touches no git ref (the branch it
        # points at was already pushed separately) — honestly not creates_ref.
        decision_fields=(FieldSpec("source_branch", Location.BODY),),
    ),
    Recognizer(
        id="mr.note",
        method="POST",
        template="/projects/{id}/merge_requests/{iid}/notes",
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
        namespace_field=None,  # request carries only the iid — resolved via MR lookup
        rule=R3,
        kind=EndpointKind.NOTE,
    ),
    Recognizer(
        id="mr.discussion",
        method="POST",
        template="/projects/{id}/merge_requests/{iid}/discussions",
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
        namespace_field=None,
        rule=R3,
        kind=EndpointKind.NOTE,
    ),
    Recognizer(
        id="mr.discussion_reply",
        method="POST",
        template="/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}/notes",
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
        namespace_field=None,
        rule=R3,
        kind=EndpointKind.NOTE,
    ),
    Recognizer(
        id="mr.update",
        method="PUT",
        template="/projects/{id}/merge_requests/{iid}",
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
        namespace_field=None,
        rule=R3,
        kind=EndpointKind.MR_UPDATE,
        # capabilities=∅ *statically* — the state_event=merge alias is
        # field-dependent, added by api_capabilities(), not declared here. The
        # capability layer alone forbids it (§07 Punkt 7: the former separate
        # "state_event != merge" check is redundant and has been removed).
        decision_fields=(FieldSpec("state_event", Location.BODY),),
    ),
    Recognizer(
        id="pipeline.trigger",
        method="POST",
        template="/projects/{id}/pipeline",
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
        namespace_field="ref",
        rule=R3,
        kind=EndpointKind.PIPELINE,
        decision_fields=(FieldSpec("ref", Location.BODY),),
    ),
    # --- extra, honestly-catalogued entries — NOT in DEFAULT_ENABLED --------
    Recognizer(
        id="branch.create",
        method="POST",
        template="/projects/{id}/repository/branches",
        scope_kind=ScopeKind.BRANCH_NAMESPACE,
        namespace_field="branch",
        rule=R3,
        kind=EndpointKind.BRANCH,
        capabilities=frozenset({Capability.CREATES_REF}),
        decision_fields=(FieldSpec("branch", Location.BODY),),
    ),
    Recognizer(
        id="issue.create",
        method="POST",
        template="/projects/{id}/issues",
        scope_kind=ScopeKind.QUOTA_BY_KIND,
        rule=R3,
        kind=EndpointKind.ISSUE,
        # capabilities=∅: an issue is not a ref, a tag, or a merge — GitLab
        # has no branch-namespace concept to gate on before creation either
        # (unlike an MR, there is no source_branch to check against the
        # namespace).
    ),
)

DEFAULT_ENABLED: frozenset[str] = frozenset(
    {
        "mr.create",
        "mr.note",
        "mr.discussion",
        "mr.discussion_reply",
        "mr.update",
        "pipeline.trigger",
    }
)


def api_capabilities(ep: Recognizer, fields: Mapping[str, object]) -> frozenset[Capability]:
    """Endpoint capabilities plus field-dependent additions.

    Every row's capabilities are static — declared on the table, independent
    of the request — with one exception: ``PUT .../merge_requests/{iid}``
    only merges when the caller also sets ``state_event=merge``. A static
    ``{merges}`` on that row would forbid the row outright (it is also the
    only way to edit an MR's title/description); a static empty set would miss
    the alias entirely.
    """
    caps = set(ep.capabilities)
    if fields.get("state_event") == "merge":
        caps.add(Capability.MERGES)
    return frozenset(caps)


def match_endpoint(entries: Iterable[Recognizer], method: str, path: str) -> Optional[Recognizer]:
    """Return the first entry in ``entries`` matching ``method``/``path``.

    ``entries`` is normally an EffectiveTable's ``.entries`` — the activated
    subset — never :data:`CATALOG` directly.
    """
    path = path.rstrip("/")
    for ep in entries:
        if ep.method == method.upper() and ep.regex.fullmatch(path):
            return ep
    return None
