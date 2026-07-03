"""The endpoint catalog (§04.2; docs/design/architecture-generalization,
§04-policy-erweiterbarkeit.md §04.2, §06-migration.md Schritt 4).

Code liefert, Config aktiviert: this table holds every GitLab REST write
endpoint the warden *knows how to guard*, whether or not a given deployment
turns it on. ``DEFAULT_ENABLED`` is exactly today's behaviour (Schritt
3's six rows) — a deployment with no ``[api.endpoints]`` section at all gets
precisely this set, unchanged. Anything else (``branch.create``,
``issue.create``) is honestly catalogued and golden-tested, but only becomes
reachable when a ``warden.toml`` explicitly enables it (``activation.py``).

The merge endpoint is deliberately **not** here — it is a built-in deny
invariant (``builtin.py``), not an activatable row. Each row's deny-probes
live in ``probes.py``, keyed by entry id, so this table stays a legible
one-row-per-endpoint list.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

from ....core.capabilities import Capability
from ....core.rules import R3
from .checks import OWNED_BY_AGENT, field_has_prefix, field_not_equals
from .model import CatalogEntry, EndpointKind, FieldSpec, Location

CATALOG: tuple[CatalogEntry, ...] = (
    # --- default set (Schritt 3 behaviour, unchanged) ----------------------
    CatalogEntry(
        id="mr.create",
        method="POST",
        template="/projects/{id}/merge_requests",
        checks=(field_has_prefix("source_branch"),),
        rule=R3,
        kind=EndpointKind.MR,
        # capabilities=∅: creating an MR touches no git ref (the branch it
        # points at was already pushed separately) — honestly not creates_ref.
        decision_fields=(FieldSpec("source_branch", Location.BODY),),
    ),
    CatalogEntry(
        id="mr.note",
        method="POST",
        template="/projects/{id}/merge_requests/{iid}/notes",
        checks=(OWNED_BY_AGENT,),
        rule=R3,
        kind=EndpointKind.NOTE,
    ),
    CatalogEntry(
        id="mr.discussion",
        method="POST",
        template="/projects/{id}/merge_requests/{iid}/discussions",
        checks=(OWNED_BY_AGENT,),
        rule=R3,
        kind=EndpointKind.NOTE,
    ),
    CatalogEntry(
        id="mr.discussion_reply",
        method="POST",
        template="/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}/notes",
        checks=(OWNED_BY_AGENT,),
        rule=R3,
        kind=EndpointKind.NOTE,
    ),
    CatalogEntry(
        id="mr.update",
        method="PUT",
        template="/projects/{id}/merge_requests/{iid}",
        checks=(OWNED_BY_AGENT, field_not_equals("state_event", "merge")),
        rule=R3,
        kind=EndpointKind.MR_UPDATE,
        # capabilities=∅ *statically* — the state_event=merge alias is
        # field-dependent, added by api_capabilities(), not declared here.
        decision_fields=(FieldSpec("state_event", Location.BODY),),
    ),
    CatalogEntry(
        id="pipeline.trigger",
        method="POST",
        template="/projects/{id}/pipeline",
        checks=(field_has_prefix("ref"),),
        rule=R3,
        kind=EndpointKind.PIPELINE,
        decision_fields=(FieldSpec("ref", Location.BODY),),
    ),
    # --- extra, honestly-catalogued entries — NOT in DEFAULT_ENABLED --------
    CatalogEntry(
        id="branch.create",
        method="POST",
        template="/projects/{id}/repository/branches",
        checks=(field_has_prefix("branch"),),
        rule=R3,
        kind=EndpointKind.BRANCH,
        capabilities=frozenset({Capability.CREATES_REF}),
        decision_fields=(FieldSpec("branch", Location.BODY),),
    ),
    CatalogEntry(
        id="issue.create",
        method="POST",
        template="/projects/{id}/issues",
        checks=(),
        rule=R3,
        kind=EndpointKind.ISSUE,
        # capabilities=∅: an issue is not a ref, a tag, or a merge — GitLab
        # has no ownership concept to gate on before creation either (unlike
        # an MR, there is no "MR the bot itself authored" check to reuse).
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


def api_capabilities(ep: CatalogEntry, fields: Mapping[str, object]) -> frozenset[Capability]:
    """Endpoint capabilities plus the one field-dependent addition (§03.4, B2).

    Every row's capabilities are static — declared on the table, independent
    of the request — with one exception: ``PUT .../merge_requests/{iid}``
    only merges when the caller also sets ``state_event=merge`` (the same
    alias :data:`~warden.guards.gitlab_api.catalog.checks.OWNED_BY_AGENT`'s
    sibling check already guards against, W6.2). A static ``{merges}`` on
    that row would forbid the row outright (it is also the only way to edit
    an MR's title/description); a static empty set would miss the alias
    entirely.
    """
    caps = set(ep.capabilities)
    if fields.get("state_event") == "merge":
        caps.add(Capability.MERGES)
    return frozenset(caps)


def match_endpoint(
    entries: Iterable[CatalogEntry], method: str, path: str
) -> Optional[CatalogEntry]:
    """Return the first entry in ``entries`` matching ``method``/``path``.

    ``entries`` is normally an
    :class:`~warden.guards.gitlab_api.catalog.activation.EffectiveTable`'s
    ``.entries`` — the activated subset — never :data:`CATALOG` directly
    (§04.2/04.3: only the effective table may decide a real request).
    """
    path = path.rstrip("/")
    for ep in entries:
        if ep.method == method.upper() and ep.regex.fullmatch(path):
            return ep
    return None
