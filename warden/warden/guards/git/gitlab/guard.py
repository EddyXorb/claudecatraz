"""GitLab REST guard I/O hooks: parse -> enrich -> forward/deny-response.

Reads stream through with the read token (R1); writes are matched against
the recognizer catalog, source-branch-namespace checked, quota-checked, then
forwarded with the write token — or denied with a 403 that never leaks a
GitLab response. /api/graphql* shares this same pipeline and is always
denied (intent.is_graphql) — an unmodelled channel, never a separate guard.

Self-contained GitLab domain logic: the MR source-branch-namespace lookup
(.mr_namespace), MR/project-id reconcile (.reconcile) and the MR-quota
table (.state) are this guard's own implementation details, reachable by no
other guard.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

import httpx
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ....core.actions import Action
from ....core.audit import AuditLog
from ....core.config import Config, normalize_project
from ....core.guard import Guard
from ....core.model import Decision, StateView, TokenKind
from ....core.state import State
from ....core.transport import UpstreamRouter, stream_upstream
from ....errors import deny_json
from .. import actions as git_actions
from . import actions as gitlab_actions
from .intent import ApiIntent
from .mr_namespace import MrNamespace
from .parsing import extract_fields, iid_from_path, project_from_path, raw_query, raw_rest_path
from .policy import decide
from .recognizers import CATALOG, RestRecognizer, ScopeKind, match_request
from .reconcile import reconcile_mrs
from .state import MrState

_DEFAULT_ACTION_IDS: frozenset[str] = frozenset(a.id for a in git_actions.DEFAULT)


def _needs_source_lookup(match: RestRecognizer) -> bool:
    """Check if the matched recognizer requires a source-branch-namespace
    lookup: a BRANCH_NAMESPACE scope whose branch is *not* literally in
    the request (namespace_field is None) — the request carries only an
    iid, so the branch must be resolved via the iid -> MR upstream lookup."""
    return match.scope_kind is ScopeKind.BRANCH_NAMESPACE and match.namespace_field is None


def _non_default_actions(recognized: frozenset[Action]) -> Optional[tuple[str, ...]]:
    """Audit marking: which recognized action ids are outside the shipped
    DEFAULT set — None when every recognized action is a default (the
    field is additive, only present once a deployment's config explicitly
    reaches beyond the default)."""
    ids = tuple(sorted(a.id for a in recognized if a.id not in _DEFAULT_ACTION_IDS))
    return ids or None


class ApiGuard(Guard[ApiIntent]):
    """The REST write pipeline's hooks — dispatched via Guard.handle from
    the routes routes returns.
    """

    @property
    def name(self) -> str:
        return "api"

    @property
    def recognizers(self) -> tuple[RestRecognizer, ...]:
        return CATALOG

    @property
    def supported_actions(self) -> frozenset[Action]:
        return gitlab_actions.SUPPORTED

    def __init__(self, cfg: Config, state: State, audit: AuditLog, router: UpstreamRouter) -> None:
        super().__init__(cfg, state, audit)
        self.router = router
        self.mr_state = MrState(state.store)
        self.mr_namespace = MrNamespace(router, cfg)
        # Numeric-id aliases of cfg.allowed_projects, resolved at reconcile.
        # Guard state, not Config — Config stays immutable; only this guard's
        # view of "which ids currently alias an allowlisted project" is refreshed.
        self.project_id_aliases: set[str] = set()
        # Built once at construction, never rebuilt — no runtime rebuild, no drift.
        # One set per configured host — an action allowed on one host can be
        # denied on another, per that host's own effective actions.
        self._effective_by_host: Mapping[str, frozenset[str]] = {
            cfg.normalize_host(ep.host): frozenset(cfg.effective_actions(ep.host))
            for ep in cfg.git_endpoints
        }

    def routes(self) -> list[Route]:
        return [
            Route(
                "/api/v4/{rest:path}",
                self.handle,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
            ),
            # GraphQL can express every write the REST filter blocks (create a
            # tag, merge an MR) in a single mutation — routed through this same
            # guard so it goes through the same pipeline, always denied
            # (intent.is_graphql), never proxied upstream.
            Route(
                "/api/graphql",
                self.handle,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
            ),
            Route(
                "/api/graphql/{rest:path}",
                self.handle,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
            ),
        ]

    def _effective_for(self, host: str) -> frozenset[str]:
        """This host's effective action ids, or an empty set if none is
        configured — every action then default-denies rather than crashing.
        A host with no [[git.endpoint]] entry can only reach here before
        the kernel's host_gate has run: parse runs ahead of it, same
        as state_view (see its docstring).
        """
        return self._effective_by_host.get(self.cfg.normalize_host(host), frozenset())

    def project_allowed(self, project: str) -> bool:
        """Check if project is allowed by path or numeric id (M6)."""
        return (
            self.cfg.project_allowed(project)
            or normalize_project(project) in self.project_id_aliases
        )

    def state_view(self, host: str) -> StateView:
        """This guard's own snapshot: its per-guard fail-safe lock + writes
        counter plus this domain's MR count — never branches (the git guard
        tracks those) — all scoped to host (per-endpoint).
        Locked until *this* guard reconciled; a broken git upstream never
        locks the REST-API guard, and vice versa.

        See GitGuard.state_view for why host is normalised but not
        resolved/validated here (this runs before host_gate).
        """
        if not self.state.is_reconciled(self.name):
            return StateView(locked=True)
        key = self.cfg.normalize_host(host)
        return StateView(
            open_mrs=self.mr_state.open_mrs(key),
            writes_last_hour=self.state.writes_last_hour(key),
            locked=False,
        )

    async def reconcile(self) -> bool:
        """Rebuild the MR counter + numeric-id aliases from GitLab truth.

        Rebuilds only this guard's own MR counter/aliases and, on success,
        unlocks only its own per-guard lock (State.mark_reconciled). A
        failure leaves *this* guard fail-safe-locked but never touches the
        git guard's lock — one guard's permanently unreachable upstream can
        never block the other.

        No endpoints configured makes no upstream call either:
        for_each_host_project simply iterates zero hosts and returns
        True, so the guard still unlocks itself and the warden serves
        (then denies) requests without ever contacting GitLab.
        """
        ok, resolved_ids = await reconcile_mrs(self.cfg, self.router, self.mr_state)
        self.project_id_aliases = resolved_ids
        if ok:
            self.state.mark_reconciled(self.name)
        return ok

    async def parse(self, request: Request) -> ApiIntent:
        """Parse method/path/project/fields/body into the decision intent.
        raw_query is kept separate from path for matching (matching stays query-less)
        but carried for forward.
        """
        rest_path = raw_rest_path(request)
        query = raw_query(request)
        method = request.method.upper()
        project = project_from_path(rest_path)
        host = request.headers.get("host", "")

        body = b"" if method in ("GET", "HEAD", "OPTIONS") else await request.body()
        intent = ApiIntent(
            _project=project,
            _method=method,
            _host=host,
            path=rest_path,
            iid=iid_from_path(rest_path),
            body=body,
            raw_query=query,
        )
        # Match once against the catalog to know which fields this specific
        # recognizer declares; the same match is recomputed (pure, cheap) by
        # the kernel and by policy.decide — see recognizers.match_request.
        match = match_request(intent)
        intent.fields = extract_fields(request, body, match)
        return intent

    async def enrich(self, intent: ApiIntent) -> ApiIntent:
        """MR source-branch-namespace lookup, only when the matched endpoint needs it.

        Reachable only once the kernel's read-only gate has already passed, so
        this read-token lookup is never made in off mode.
        """
        match = match_request(intent)
        needs_lookup = match is not None and _needs_source_lookup(match)
        if needs_lookup and intent.iid is not None and intent.project:
            intent.mr_source_ok = await self.mr_namespace.source_in_namespace(
                intent.host, intent.project, intent.iid
            )
        return intent

    def decide(self, intent: ApiIntent, state: StateView, cfg: Config) -> Decision:
        return decide(intent, state, cfg, self._effective_for(intent.host))

    def record(self, intent: ApiIntent, decision: Decision) -> None:
        """Record the write *before* the upstream call — fail-safe against crashes."""
        if decision.token != TokenKind.WRITE:
            return
        match = match_request(intent)
        assert match is not None, "an allowed write always matched a recognizer"
        recognized = match(intent) or frozenset()
        action = next(iter(recognized), None)
        quota_kind = gitlab_actions.QUOTA_KIND.get(action.id) if action is not None else None
        assert quota_kind is not None, "an allowed write's action always has a quota kind"
        host = self.cfg.resolve_target_host(intent.host)
        assert host is not None, "kernel_gates already denied an unresolved host"
        ref_or_iid = str(intent.iid or intent.project)
        self.state.record_write("api", host, quota_kind.value, ref_or_iid)

    async def forward(self, request: Request, intent: ApiIntent, decision: Decision) -> Response:
        # Raw query is reattached here at transport boundary only, never in intent.path
        # — matching/decision stay query-less.
        transport = self.router.resolve(intent.host)
        assert transport is not None, "kernel_gates already denied an unresolved host"
        path = f"{intent.path}?{intent.raw_query}" if intent.raw_query else intent.path
        resp: httpx.Response = await transport.open_rest(
            intent.method,
            path,
            headers=dict(request.headers),
            content=intent.body or None,
            token=decision.token,
        )
        return stream_upstream(resp)

    def deny_response(self, intent: ApiIntent, decision: Decision, state: StateView) -> Response:
        return deny_json(decision)

    def audit_fields(self, intent: ApiIntent) -> Mapping[str, Any]:
        match = match_request(intent)
        recognized = (match(intent) or frozenset()) if match is not None else frozenset()
        action = next(iter(recognized), None)
        quota_kind = gitlab_actions.QUOTA_KIND.get(action.id) if action is not None else None
        fields: dict[str, Any] = {
            "path": intent.path,
            "kind": quota_kind.value if quota_kind is not None else None,
        }
        if recognized:
            fields["actions"] = tuple(sorted(a.id for a in recognized))
        non_default = _non_default_actions(recognized)
        if non_default is not None:
            fields["enabled_via"] = non_default
        return fields
