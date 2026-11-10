"""GitHub REST guard I/O hooks: parse -> enrich -> forward/deny-response.

Writes are matched against the recognizer catalog, scope-checked (branch
namespace, or the addressed pull request / CI run being the agent's),
quota-checked, then forwarded with the write token or denied."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ....core.actions import Action
from ....core.audit import AuditLog
from ....core.config import Config
from ....core.guard import Guard
from ....core.model import Decision, StateView, TokenKind
from ....core.state import State
from ....core.transport import UpstreamRouter, stream_upstream
from ....errors import deny_json
from .. import actions as git_actions
from ..endpoints import composes
from ..mr_state import MrState
from . import actions as github_actions
from . import policy
from .intent import GithubIntent
from .lookup import GithubLookups
from .parsing import (
    decision_fields,
    job_id_from_path,
    number_from_path,
    parse_json_object,
    project_from_path,
    raw_query,
    raw_rest_path,
    run_id_from_path,
)
from .recognizers import CATALOG, GithubRecognizer, Scope, match_request
from .reconcile import reconcile_pulls

_DEFAULT_ACTION_IDS: frozenset[str] = frozenset(a.id for a in git_actions.DEFAULT)

# Allowlist, not blocklist: nothing else the agent sends reaches GitHub.
_FORWARDED_HEADERS = frozenset(
    {
        "accept",
        "content-type",
        "user-agent",
        "x-github-api-version",
        "if-none-match",
        "if-modified-since",
    }
)


class GithubGuard(Guard[GithubIntent]):
    """The /api/v3 pipeline's hooks — dispatched via Guard.handle."""

    @property
    def name(self) -> str:
        return "github"

    @property
    def recognizers(self) -> tuple[GithubRecognizer, ...]:
        return CATALOG

    @property
    def supported_actions(self) -> frozenset[Action]:
        return github_actions.SUPPORTED

    def __init__(self, cfg: Config, state: State, audit: AuditLog, router: UpstreamRouter) -> None:
        super().__init__(cfg, state, audit)
        self.router = router
        self.mr_state = MrState(state.store)
        self.lookups = GithubLookups(router, cfg)

    def routes(self) -> list[Route]:
        return [
            Route(
                "/api/v3/{rest:path}",
                self.handle,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
            ),
        ]

    def serves_host(self, host: str) -> bool:
        return composes(self.cfg, host, "github")

    def state_view(self, host: str) -> StateView:
        """Own fail-safe lock, writes counter and open-MR count, scoped to host."""
        if not self.state.is_reconciled(self.name):
            return StateView(locked=True)
        key = self.cfg.normalize_host(host)
        return StateView(
            open_mrs=self.mr_state.open_mrs(key),
            writes_last_hour=self.state.writes_last_hour(key),
            locked=False,
        )

    async def reconcile(self) -> bool:
        ok = await reconcile_pulls(self.cfg, self.router, self.mr_state)
        if ok:
            self.state.mark_reconciled(self.name)
        return ok

    def _may_look_up(self, intent: GithubIntent) -> bool:
        """The kernel gates an issue-kind lookup would otherwise precede:
        it runs only for a writable, served host and an allowed project."""
        host = intent.host
        return (
            intent.number is not None
            and bool(intent.project)
            and self.serves_host(host)
            and self.cfg.host_allowed(host)
            and self.cfg.access_mode(host) == "read-write"
            and self.project_allowed(host, intent.project)
        )

    async def parse(self, request: Request) -> GithubIntent:
        """Parse path/query/body into the intent. An issue-number write is
        resolved to pull request or issue here, since recognition needs it."""
        path = raw_rest_path(request)
        method = request.method.upper()
        intent = GithubIntent(
            _project=project_from_path(path),
            _method=method,
            _host=request.headers.get("host", ""),
            path=path,
            number=number_from_path(path),
            run_id=run_id_from_path(path),
            job_id=job_id_from_path(path),
            raw_query=raw_query(request),
        )
        match = match_request(intent)
        if not intent.needs_write:
            return intent
        intent.body = await request.body()
        data = parse_json_object(intent.body)
        fields = decision_fields(data, match.decision_fields if match is not None else ())
        intent.body_ok = fields is not None
        intent.fields = fields or {}
        intent.body_keys = frozenset(data) if data is not None else frozenset()
        if match is not None and match.scope is Scope.ISSUE_KIND and self._may_look_up(intent):
            assert intent.number is not None
            intent.issue_kind = await self.lookups.issue_kind(
                intent.host, intent.project, intent.number
            )
        return intent

    async def enrich(self, intent: GithubIntent) -> GithubIntent:
        """Resolve whose pull request / run / job a write addresses; reached
        only once the kernel gates have passed."""
        match = match_request(intent)
        if match is None or not intent.needs_write:
            return intent
        scope = match.effective_scope(intent)
        host, project = intent.host, intent.project
        if scope is Scope.PULL and intent.number is not None:
            intent.scope_ok = await self.lookups.pull_in_namespace(host, project, intent.number)
        elif scope is Scope.RUN and intent.run_id is not None:
            intent.scope_ok = await self.lookups.run_in_namespace(host, project, intent.run_id)
        elif scope is Scope.JOB and intent.job_id is not None:
            intent.scope_ok = await self.lookups.job_in_namespace(host, project, intent.job_id)
        return intent

    def decide(self, intent: GithubIntent, state: StateView, cfg: Config) -> Decision:
        return policy.decide(intent, state, cfg)

    def _action(self, intent: GithubIntent) -> Optional[Action]:
        _, recognized = policy.recognize(intent)
        return next(iter(recognized), None)

    def record(self, intent: GithubIntent, decision: Decision) -> None:
        """Record the write before the upstream call — fail-safe against crashes."""
        if decision.token != TokenKind.WRITE:
            return
        action = self._action(intent)
        assert action is not None and action.quota_kind is not None, (
            "an allowed write always recognized an action with a quota kind"
        )
        host = self.cfg.resolve_target_host(intent.host)
        assert host is not None, "kernel_gates already denied an unresolved host"
        ref = intent.number or intent.run_id or intent.job_id
        self.state.record_write(self.name, host, action.quota_kind, str(ref or intent.project))

    async def forward(self, request: Request, intent: GithubIntent, decision: Decision) -> Response:
        transport = self.router.resolve(intent.host)
        assert transport is not None, "kernel_gates already denied an unresolved host"
        headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARDED_HEADERS}
        path = f"{intent.path}?{intent.raw_query}" if intent.raw_query else intent.path
        resp = await transport.open_rest(
            intent.method, path, headers=headers, content=intent.body or None, token=decision.token
        )
        return stream_upstream(resp)

    def deny_response(self, intent: GithubIntent, decision: Decision, state: StateView) -> Response:
        return deny_json(decision)

    def audit_fields(self, intent: GithubIntent) -> Mapping[str, Any]:
        _, recognized = policy.recognize(intent)
        action = next(iter(recognized), None)
        fields: dict[str, Any] = {
            "path": intent.path,
            "kind": action.quota_kind if action is not None else None,
        }
        if recognized:
            fields["actions"] = tuple(sorted(a.id for a in recognized))
        non_default = tuple(sorted(a.id for a in recognized if a.id not in _DEFAULT_ACTION_IDS))
        if non_default:
            fields["enabled_via"] = non_default
        match = match_request(intent)
        if match is not None and match.scope is Scope.ISSUE_KIND:
            fields["issue_kind"] = intent.issue_kind.value
        return fields
