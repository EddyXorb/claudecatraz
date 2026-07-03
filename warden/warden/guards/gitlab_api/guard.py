"""REST guard I/O hooks: parse → enrich → forward/deny-response —
the guard half of the write pipeline. Reads stream through with the read-token (R1);
writes are matched against the data-driven allowlist, ownership-checked,
quota-checked, then forwarded with the write-token — or denied with a 403
that never leaks a GitLab response.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

import httpx
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...core.audit import AuditLog
from ...core.config import Config
from ...core.guard import Guard
from ...core.model import Decision, StateView, TokenKind
from ...core.rules import R6
from ...core.state import State
from ...errors import deny_json
from ..gitlab.forge import GitForge
from ..gitlab.upstream import stream_upstream
from .catalog import CatalogEntry, EffectiveTable, build_effective_table, match_endpoint
from .intent import ApiIntent, GraphqlIntent
from .parsing import extract_fields, iid_from_path, project_from_path, raw_query, raw_rest_path
from .policy import capability_gate, decide


def _needs_mr_owner(ep: CatalogEntry) -> bool:
    """Check if the matched entry requires MR ownership verification by declared need."""
    return any("mr_owner" in check.needs for check in ep.checks)


class ApiGuard(Guard[ApiIntent]):
    """The REST write pipeline's hooks — dispatched via
    :meth:`Guard.handle` from the route :meth:`routes` returns.
    """

    @property
    def name(self) -> str:
        return "api"

    def __init__(self, cfg: Config, state: State, audit: AuditLog, forge: GitForge) -> None:
        super().__init__(cfg, state, audit)
        self.forge = forge
        # Built once at construction, never rebuilt — no runtime rebuild, no drift.
        self._effective: EffectiveTable = build_effective_table(cfg, cfg.endpoint_enable)

    def routes(self) -> list[Route]:
        return [
            Route(
                "/api/v4/{rest:path}",
                self.handle,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
            )
        ]

    def project_allowed(self, project: str) -> bool:
        """Check if project is allowed by path or numeric id."""
        return self.cfg.project_allowed(project) or self.forge.project_allowed_by_id(project)

    def state_view(self) -> StateView:
        return self.forge.state_view()

    async def reconcile(self) -> bool:
        return await self.forge.reconcile()

    async def parse(self, request: Request) -> ApiIntent:
        """Parse method/path/project/fields/body into the decision intent.
        ``raw_query`` is kept separate from path for matching (matching stays query-less)
        but carried for :meth:`forward`.
        """
        rest_path = raw_rest_path(request)
        query = raw_query(request)
        method = request.method.upper()
        project = project_from_path(rest_path)

        body = b"" if method in ("GET", "HEAD", "OPTIONS") else await request.body()
        # Match against the effective table only — never the catalog directly.
        ep = (
            None
            if method in ("GET", "HEAD", "OPTIONS")
            else match_endpoint(self._effective.entries, method, rest_path)
        )
        fields = extract_fields(request, body, ep)

        return ApiIntent(
            _project=project,
            _method=method,
            path=rest_path,
            endpoint=ep,
            fields=fields,
            iid=iid_from_path(rest_path),
            body=body,
            raw_query=query,
        )

    async def enrich(self, intent: ApiIntent) -> ApiIntent:
        """MR source-branch-namespace lookup, only when the matched endpoint needs it.

        Reachable only once the kernel's read-only gate has already passed, so
        this read-token lookup is never made in off mode.
        """
        ep = intent.endpoint
        if ep is not None and _needs_mr_owner(ep) and intent.iid is not None and intent.project:
            intent.mr_source_ok = await self.forge.mr_source_in_namespace(
                intent.project, intent.iid
            )
        return intent

    def capability_gate(self, intent: ApiIntent, cfg: Config) -> Optional[Decision]:
        return capability_gate(intent, cfg, self._effective)

    def decide(self, intent: ApiIntent, state: StateView, cfg: Config) -> Decision:
        return decide(intent, state, cfg, self._effective)

    def record(self, intent: ApiIntent, decision: Decision) -> None:
        """Record the write *before* the upstream call — fail-safe against crashes."""
        if decision.token == TokenKind.WRITE and intent.endpoint is not None:
            self.state.record_write("api", intent.endpoint.kind, str(intent.iid or intent.project))

    async def forward(self, request: Request, intent: ApiIntent, decision: Decision) -> Response:
        # Raw query is reattached here at transport boundary only, never in intent.path
        # — matching/decision stay query-less.
        path = f"{intent.path}?{intent.raw_query}" if intent.raw_query else intent.path
        resp: httpx.Response = await self.forge.upstream.open_rest(
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
        fields: dict[str, Any] = {
            "path": intent.path,
            "kind": intent.endpoint.kind if intent.endpoint else None,
        }
        via = self._enabled_via(intent)
        if via is not None:
            fields["enabled_via"] = via
        return fields

    def _enabled_via(self, intent: ApiIntent) -> Optional[str]:
        """Audit marking for a non-default-activated catalog entry.

        Returns ``None`` for the shipped default set (and for no match) — the field is
        additive and only shows up when a deployment explicitly enabled it via config.
        """
        if intent.endpoint is None:
            return None
        via = self._effective.enabled_via.get(intent.endpoint.id)
        return via if via and via != "default" else None


class GraphqlGuard(Guard[GraphqlIntent]):
    """`/api/graphql` — always 403, never contacts upstream.

    GitLab's GraphQL API can express every write the REST filter blocks (create
    a tag, merge an MR) in a single mutation; routing it would silently bypass
    R2-R4, so this guard denies unconditionally instead of proxying.
    """

    @property
    def name(self) -> str:
        return "api"

    def routes(self) -> list[Route]:
        return [
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

    async def parse(self, request: Request) -> GraphqlIntent:
        return GraphqlIntent(path=request.url.path, _method=request.method)

    async def enrich(self, intent: GraphqlIntent) -> GraphqlIntent:
        return intent

    def capability_gate(self, intent: GraphqlIntent, cfg: Config) -> Optional[Decision]:
        return None

    def decide(self, intent: GraphqlIntent, state: StateView, cfg: Config) -> Decision:
        return Decision(False, R6, "GraphQL is not permitted — unmodelled channel")

    def record(self, intent: GraphqlIntent, decision: Decision) -> None:
        pass

    async def forward(
        self, request: Request, intent: GraphqlIntent, decision: Decision
    ) -> Response:
        raise AssertionError("unreachable — decide() always denies")

    def deny_response(
        self, intent: GraphqlIntent, decision: Decision, state: StateView
    ) -> Response:
        return deny_json(decision)

    def audit_fields(self, intent: GraphqlIntent) -> Mapping[str, Any]:
        return {"path": intent.path, "kind": None}
