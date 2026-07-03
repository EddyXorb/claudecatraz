"""REST guard I/O hooks (§03.2, W6): parse → enrich → forward/deny-response —
the guard half of the write pipeline; :mod:`warden.guards.gitlab_api.policy`
holds the pure decision half. Reads stream through with the read-token (R1);
writes are matched against the data-driven allowlist, ownership-checked,
quota-checked, then forwarded with the write-token — or denied with a 403
that never leaks a GitLab response.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Mapping, Optional

import httpx
from starlette.requests import Request
from starlette.responses import Response

from ...core.audit import AuditEvent
from ...core.config import Config
from ...core.guard import run_guarded
from ...core.model import Decision, StateView, TokenKind
from ...core.rules import R6
from ...errors import deny_json
from .catalog import CatalogEntry, match_endpoint
from .context import AppContext
from .intent import ApiIntent
from .parsing import extract_fields, iid_from_path, project_from_path, raw_query, raw_rest_path
from .policy import capability_gate, decide
from .upstream import stream_upstream


def _needs_mr_owner(ep: CatalogEntry) -> bool:
    """F2 fix: does any check on the matched entry declare a need for the MR
    ownership lookup — instead of testing function identity
    (``mr_owned_by_claude in ep.checks``) against a hardcoded predicate.
    """
    return any("mr_owner" in check.needs for check in ep.checks)


class ApiGuard:
    """The REST write pipeline's hooks (§03.2) — driven by
    :func:`core.guard.run_guarded` from :func:`handle` below.
    """

    # Audit ``channel`` value — kept as the pre-Schritt-5 bare string for
    # byte-compatible JSONL (the channel→guard rename is §06 Schritt 6).
    name = "api"

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def parse(self, request: Request) -> ApiIntent:
        """Parse method/path/project/fields/body into the decision intent
        (W6, §6.9). ``ApiIntent.raw_query`` is kept out of ``path`` (matching
        stays query-less, F12) but carried for :meth:`forward`.
        """
        rest_path = raw_rest_path(request)
        query = raw_query(request)
        method = request.method.upper()
        project = project_from_path(rest_path)

        body = b"" if method in ("GET", "HEAD", "OPTIONS") else await request.body()
        # §04.3: match against the effective table (Catalog × config), never
        # the catalog itself — a deployment's [api.endpoints] genuinely
        # decides what is reachable here.
        ep = (
            None
            if method in ("GET", "HEAD", "OPTIONS")
            else match_endpoint(self.ctx.cfg.effective_endpoints.entries, method, rest_path)
        )
        fields = extract_fields(request, body, ep)

        return ApiIntent(
            project=project,
            method=method,
            path=rest_path,
            endpoint=ep,
            fields=fields,
            iid=iid_from_path(rest_path),
            body=body,
            raw_query=query,
        )

    async def enrich(self, intent: ApiIntent) -> ApiIntent:
        """MR ownership lookup (W6.2), only when the matched endpoint needs it.

        Reachable only once the kernel's read-only gate has already passed
        (§03.2) — the write credential this transitively depends on (via
        :meth:`AppContext.resolve_service_account`) is therefore never
        touched in off/read-only mode, replacing the manual
        ``ctx.cfg.writes_enabled`` guard this method used to carry itself
        (pre-Schritt-5 ``api_proxy.py:102``).
        """
        ep = intent.endpoint
        if ep is not None and _needs_mr_owner(ep) and intent.iid is not None and intent.project:
            intent.mr_owner_ok = await self.ctx.mr_owned_by_claude(intent.project, intent.iid)
        return intent

    def capability_gate(self, intent: ApiIntent, cfg: Config) -> Optional[Decision]:
        return capability_gate(intent, cfg)

    def decide(self, intent: ApiIntent, state: StateView, cfg: Config) -> Decision:
        return decide(intent, state, cfg)

    def record(self, intent: ApiIntent, decision: Decision) -> None:
        """Record the write *before* the upstream call (idempotency / fail-safe, §6.11)."""
        if decision.token == TokenKind.WRITE and intent.endpoint is not None:
            self.ctx.state.record_write(
                "api", intent.endpoint.kind, str(intent.iid or intent.project)
            )

    async def forward(self, request: Request, intent: ApiIntent, decision: Decision) -> Response:
        # F12: the raw query is reattached here, at the transport boundary,
        # never folded into intent.path — matching/decision stay query-less.
        path = f"{intent.path}?{intent.raw_query}" if intent.raw_query else intent.path
        resp: httpx.Response = await self.ctx.upstream.open_rest(
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
        """Audit marking for a non-default-activated catalog entry (§04.3).

        Returns ``None`` for the shipped default set (and for no match at
        all) — the field is additive and only shows up when it actually says
        something (§04.3 deviation from the ``rule = "gitlab.R3+enabled:…"``
        sketch in ``04-policy-erweiterbarkeit.md``: a dedicated field instead
        of a rule-id suffix, documented there).
        """
        if intent.endpoint is None:
            return None
        via = self.ctx.cfg.effective_endpoints.enabled_via.get(intent.endpoint.id)
        return via if via and via != "default" else None


async def handle(request: Request) -> Response:
    ctx: AppContext = request.app.state.ctx
    return await run_guarded(ApiGuard(ctx), request, ctx.cfg, ctx.state, ctx.audit)


async def deny_graphql(request: Request) -> Response:
    """`/api/graphql` — always 403, always audited, never contacts upstream (B5).

    GitLab's GraphQL API can express every write the REST filter blocks (create
    a tag, merge an MR) in a single mutation; routing it would silently bypass
    R2–R4. This handler makes the refusal *designed*, not merely "not wired up"
    (§06-migration.md Anti-Ziele) — the app's routes point every `/api/graphql`
    method here instead of proxying, so the deny is intentional and logged like
    any other decision. Outside the kernel pipeline on purpose (§03.2's "dünne
    Handler" carve-out): there is no Intent to parse, only an unconditional deny.
    """
    ctx: AppContext = request.app.state.ctx
    correlation_id = str(uuid.uuid4())
    started = time.monotonic()
    decision = Decision(False, R6, "GraphQL is not permitted — unmodelled channel")
    state = ctx.state.view()
    ctx.audit.log(
        AuditEvent(
            channel="api",
            correlation_id=correlation_id,
            method=request.method,
            project="",
            decision=decision,
            state=state,
            started=started,
            upstream_status=None,
            extra={"path": request.url.path, "kind": None},
        )
    )
    return deny_json(decision)
