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
invariant (``builtin.py``), not an activatable row.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

from ..capabilities import Capability
from ..config import Config
from ..model import Decision, ProxyRequest, StateView
from ..rules import R2, R3
from .checks import OWNED_BY_AGENT, field_has_prefix, field_not_equals
from .model import (
    OTHER_PROJECT_PATH,
    PROBE_PROJECT_PATH,
    CatalogEntry,
    DenyProbe,
    EndpointKind,
    FieldSpec,
    Location,
    OverridableParam,
    RegisteredCheck,
)


def _literal_branch_prefix_check(prefix: str) -> RegisteredCheck:
    """Build the override replacement for ``branch.create``'s namespace check
    (§04.3): a *literal* prefix, tighter than the deployment-wide
    ``branch_prefixes`` union that :func:`field_has_prefix` otherwise checks.
    """

    def check(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
        value = req.fields.get("branch", "")
        if isinstance(value, str) and value.startswith(prefix):
            return None
        return Decision(False, R2, f"branch {value!r} outside required prefix {prefix!r}")

    return RegisteredCheck(name=f"field_has_prefix('branch', literal={prefix!r})", fn=check)


def _branch_prefix_is_narrower(cfg: Config, value: object) -> bool:
    # An override prefix narrows the default iff everything it matches would
    # already have matched the deployment's own namespace — reuses
    # Config.in_branch_namespace instead of re-deriving the namespace rule.
    return isinstance(value, str) and bool(value) and cfg.in_branch_namespace(value)


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
        deny_probes=(
            DenyProbe(
                description="source_branch outside the branch namespace is denied",
                method="POST",
                path=f"/projects/{PROBE_PROJECT_PATH}/merge_requests",
                fields={"source_branch": "main", "target_branch": "main"},
            ),
        ),
    ),
    CatalogEntry(
        id="mr.note",
        method="POST",
        template="/projects/{id}/merge_requests/{iid}/notes",
        checks=(OWNED_BY_AGENT,),
        rule=R3,
        kind=EndpointKind.NOTE,
        deny_probes=(
            DenyProbe(
                description="a note on an MR whose ownership can't be verified is denied",
                method="POST",
                path=f"/projects/{PROBE_PROJECT_PATH}/merge_requests/7/notes",
                fields={"body": "hi"},
            ),
        ),
    ),
    CatalogEntry(
        id="mr.discussion",
        method="POST",
        template="/projects/{id}/merge_requests/{iid}/discussions",
        checks=(OWNED_BY_AGENT,),
        rule=R3,
        kind=EndpointKind.NOTE,
        deny_probes=(
            DenyProbe(
                description="a discussion on an unverifiable MR is denied",
                method="POST",
                path=f"/projects/{PROBE_PROJECT_PATH}/merge_requests/7/discussions",
                fields={"body": "nit"},
            ),
        ),
    ),
    CatalogEntry(
        id="mr.discussion_reply",
        method="POST",
        template="/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}/notes",
        checks=(OWNED_BY_AGENT,),
        rule=R3,
        kind=EndpointKind.NOTE,
        deny_probes=(
            DenyProbe(
                description="a discussion reply on an unverifiable MR is denied",
                method="POST",
                path=(
                    f"/projects/{PROBE_PROJECT_PATH}/merge_requests/7/"
                    "discussions/abc123/notes"
                ),
                fields={"body": "done"},
            ),
        ),
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
        deny_probes=(
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
    ),
    CatalogEntry(
        id="pipeline.trigger",
        method="POST",
        template="/projects/{id}/pipeline",
        checks=(field_has_prefix("ref"),),
        rule=R3,
        kind=EndpointKind.PIPELINE,
        decision_fields=(FieldSpec("ref", Location.BODY),),
        deny_probes=(
            DenyProbe(
                description="triggering a pipeline on a protected ref is denied",
                method="POST",
                path=f"/projects/{PROBE_PROJECT_PATH}/pipeline",
                fields={"ref": "main"},
            ),
        ),
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
        deny_probes=(
            DenyProbe(
                description="creating a branch outside the namespace via REST is denied",
                method="POST",
                path=f"/projects/{PROBE_PROJECT_PATH}/repository/branches",
                fields={"branch": "main", "ref": "main"},
            ),
        ),
        # §04.2/04.3: a deployment may narrow this entry's namespace check to
        # a literal prefix tighter than the general branch_prefixes union —
        # never wider. Demonstrates the override mechanism end to end; no
        # default entry needs it (none of the shipped six take a literal
        # prefix parameter to begin with).
        overridable=(
            OverridableParam(
                key="branch_prefix",
                check_index=0,
                is_narrower=_branch_prefix_is_narrower,
                rebuild=lambda cfg, value: _literal_branch_prefix_check(str(value)),
            ),
        ),
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
        deny_probes=(
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
    alias :data:`~warden.catalog.checks.OWNED_BY_AGENT`'s sibling check
    already guards against, W6.2). A static ``{merges}`` on that row would
    forbid the row outright (it is also the only way to edit an MR's
    title/description); a static empty set would miss the alias entirely.
    """
    caps = set(ep.capabilities)
    if fields.get("state_event") == "merge":
        caps.add(Capability.MERGES)
    return frozenset(caps)


def match_endpoint(
    entries: Iterable[CatalogEntry], method: str, path: str
) -> Optional[CatalogEntry]:
    """Return the first entry in ``entries`` matching ``method``/``path``.

    ``entries`` is normally an :class:`~warden.catalog.activation.EffectiveTable`'s
    ``.entries`` — the activated, override-applied subset — never
    :data:`CATALOG` directly (§04.2/04.3: only the effective table may decide
    a real request).
    """
    path = path.rstrip("/")
    for ep in entries:
        if ep.method == method.upper() and ep.regex.fullmatch(path):
            return ep
    return None
