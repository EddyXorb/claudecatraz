"""Shared builders for the GitHub guard tests: config, app harness, upstream JSON."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional
from urllib.parse import unquote

import httpx
import respx

from warden.app import create_app
from warden.context import AppContext, build_context
from warden.core.audit import AuditEvent, AuditLog
from warden.core.config import Config, GitEndpoint, GitRules, HostCredentials
from warden.core.state import State
from warden.guards.git.endpoints import ENDPOINT_TYPES
from warden.guards.git.github.intent import GithubIntent, IssueKind
from warden.guards.git.github.parsing import (
    job_id_from_path,
    number_from_path,
    project_from_path,
    run_id_from_path,
)

HOST = "github.com"
API = "https://api.github.com"
PROJECT = "octo/repo"
REPO = f"/api/v3/repos/{PROJECT}"
UP = f"{API}/repos/{PROJECT}"
READ, WRITE = "ghp_READSECRET", "ghp_WRITESECRET"
ALL_GITHUB_ACTIONS = tuple(sorted(ENDPOINT_TYPES["github"].valid_action_ids))


def endpoint(host: str = HOST, **kw: Any) -> GitEndpoint:
    kw.setdefault("allowed_projects", (PROJECT,))
    return GitEndpoint(host=host, type="github", **kw)


def make_cfg(
    *endpoints: GitEndpoint,
    creds: Optional[dict[str, HostCredentials]] = None,
    actions: Optional[tuple[str, ...]] = None,
    rules: Optional[GitRules] = None,
) -> Config:
    """One github.com endpoint with read+write tokens unless overridden."""
    eps = endpoints or (endpoint(actions=actions, rules=rules or GitRules()),)
    if creds is None:
        creds = {
            Config.normalize_host(e.host): HostCredentials(read_token=READ, write_token=WRITE)
            for e in eps
        }
    return Config(state_db_path=":memory:", git_endpoints=eps, git_credentials=creds)


class CaptureAudit(AuditLog):
    """Keeps every event and the redacted record the log would write."""

    def __init__(self) -> None:
        super().__init__("-")
        self.events: list[AuditEvent] = []
        self.records: list[dict[str, Any]] = []

    def log(self, entry: Any) -> None:
        self.events.append(entry)
        super().log(entry)
        self.records.append(self._queue.get_nowait())


@dataclass
class Harness:
    cfg: Config
    state: State
    ctx: AppContext
    client: httpx.AsyncClient
    mock: respx.MockRouter
    audit: CaptureAudit
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def last(self) -> dict[str, Any]:
        return self.audit.records[-1]

    def forward_route(self, base: str = API) -> respx.Route:
        """Catch-all upstream route; add after the specific lookup routes."""
        return self.mock.route(url__startswith=base).respond(200, json={"ok": True})


@contextlib.asynccontextmanager
async def harness(
    cfg: Optional[Config] = None,
    *,
    host: str = HOST,
    reconciled: bool = True,
    raise_app_exceptions: bool = True,
) -> AsyncIterator[Harness]:
    cfg = cfg or make_cfg()
    state = State(":memory:")
    if reconciled:
        for guard in ("git", "api", "github"):
            state.mark_reconciled(guard)
    audit = CaptureAudit()
    ctx = build_context(cfg, state, audit)
    transport = httpx.ASGITransport(app=create_app(ctx), raise_app_exceptions=raise_app_exceptions)
    with respx.mock(assert_all_called=False) as mock:
        async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}") as client:
            yield Harness(cfg, state, ctx, client, mock, audit)
    await ctx.router.aclose()


def pull_json(
    ref: str = "claude/x", head_id: Optional[int] = 1, base_id: int = 1, number: int = 7
) -> dict[str, Any]:
    head_repo = None if head_id is None else {"id": head_id}
    return {
        "number": number,
        "head": {"ref": ref, "repo": head_repo},
        "base": {"ref": "main", "repo": {"id": base_id}},
    }


def run_json(branch: str = "claude/x", head_id: int = 1, repo_id: int = 1) -> dict[str, Any]:
    return {
        "head_branch": branch,
        "head_repository": {"id": head_id},
        "repository": {"id": repo_id},
    }


def mock_owned_lookups(mock: respx.MockRouter, base: str = UP) -> None:
    """Lookups that make PR 7, run 9 and job 11 the agent's own."""
    mock.get(f"{base}/pulls/7").respond(200, json=pull_json())
    mock.get(f"{base}/issues/7").respond(200, json={"number": 7, "pull_request": {}})
    mock.get(f"{base}/actions/runs/9").respond(200, json=run_json())
    mock.get(f"{base}/actions/jobs/11").respond(200, json={"run_id": 9})


def intent(
    method: str,
    path: str,
    *,
    fields: Optional[dict[str, str]] = None,
    body_keys: tuple[str, ...] = (),
    body_ok: bool = True,
    raw_query: str = "",
    issue_kind: IssueKind = IssueKind.UNKNOWN,
    scope_ok: Optional[bool] = None,
    host: str = HOST,
) -> GithubIntent:
    """A parsed intent for a REST path below /api/v3."""
    return GithubIntent(
        _project=project_from_path(path),
        _method=method,
        _host=host,
        path=path,
        fields=fields or {},
        body_keys=frozenset(body_keys),
        body_ok=body_ok,
        number=number_from_path(path),
        run_id=run_id_from_path(path),
        job_id=job_id_from_path(path),
        raw_query=raw_query,
        issue_kind=issue_kind,
        scope_ok=scope_ok,
    )


async def raw_asgi(
    app: Any, method: str, raw_path: str, *, host: str = HOST, body: bytes = b""
) -> tuple[int, bytes]:
    """Drive the app with an un-normalised raw path (httpx strips dot segments)."""
    path, _, query = raw_path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": unquote(path),
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(b"host", host.encode()), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1),
        "server": (host, 80),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, data
