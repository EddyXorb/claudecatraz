"""Starlette app + routing: API vs. git, agent port vs. admin port (W3, W4)."""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import api_proxy, git_proxy
from .catalog import endpoint_table_report
from .context import AppContext

# Static log-viewer page (O.4) — a package asset, not routing code (F7).
_VIEWER_HTML_PATH = Path(__file__).parent / "static" / "viewer.html"
_VIEWER_HTML = _VIEWER_HTML_PATH.read_text(encoding="utf-8")


def create_app(ctx: AppContext) -> Starlette:
    """Agent-facing app on port 8080: API proxy + git Smart-HTTP (W4)."""
    # The /git/ prefix is load-bearing: it separates git Smart-HTTP routes from
    # /api/v4/… on the same port. Repos keep their canonical remote URL
    # (https://gitlab.com/…); the entrypoint injects a global git insteadOf rewrite
    # (https://gitlab.com/ → http://gitlab-warden:8080/git/) so the prefix is added
    # transparently at transport time without touching .git/config. See W3.1.
    routes = [
        Route("/git/{project:path}/info/refs", git_proxy.advertise, methods=["GET"]),
        Route("/git/{project:path}/git-upload-pack", git_proxy.upload_pack, methods=["POST"]),
        Route("/git/{project:path}/git-receive-pack", git_proxy.receive_pack, methods=["POST"]),
        Route(
            "/api/v4/{rest:path}",
            api_proxy.handle,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
        ),
        # GraphQL is a deliberate dead end (B5): never proxied, always 403 + audited
        # — see api_proxy.deny_graphql and §06-migration.md's Anti-Ziele.
        Route(
            "/api/graphql",
            api_proxy.deny_graphql,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        ),
        Route(
            "/api/graphql/{rest:path}",
            api_proxy.deny_graphql,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        ),
        Route("/healthz", _healthz, methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    app.state.ctx = ctx
    return app


def create_admin_app(ctx: AppContext) -> Starlette:
    """Admin app on port 9090: healthz + read-only log tail + viewer (W3, §6.8, O.4)."""
    routes = [
        Route("/healthz", _healthz, methods=["GET"]),
        Route("/audit", _audit_tail, methods=["GET"]),
        Route("/policy", _policy, methods=["GET"]),
        Route("/", _viewer, methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    app.state.ctx = ctx
    return app


async def _healthz(request: Request) -> JSONResponse:
    ctx: AppContext = request.app.state.ctx
    return JSONResponse(
        {
            "status": "ok",
            "reconciled": ctx.state.is_reconciled(),
            "service_account_id": ctx.service_account_id,
        }
    )


async def _audit_tail(request: Request) -> Response:
    """Read-only tail of the JSONL audit log (admin net only)."""
    ctx: AppContext = request.app.state.ctx
    path = ctx.cfg.audit_log_path
    try:
        with open(path, "rb") as fh:
            lines = fh.readlines()[-200:]
    except OSError:
        return PlainTextResponse("", status_code=200)
    return PlainTextResponse(b"".join(lines).decode("utf-8", "replace"))


async def _policy(request: Request) -> JSONResponse:
    """Read-only summary of the effective endpoint catalog (§04.3, admin net
    only): every catalog entry, whether it's part of the default set, and
    whether this deployment actually activated it. ``catraz doctor``/
    ``catraz allow-endpoint`` are the CLI front for this route — it is how
    the CLI learns catalog ids and activation state without shipping (or
    running) a copy of the warden's Python.
    """
    ctx: AppContext = request.app.state.ctx
    return JSONResponse(endpoint_table_report(ctx.cfg))


async def _viewer(request: Request) -> HTMLResponse:
    """Static log viewer (O.4): filters JSONL by channel/rule/decision/project."""
    return HTMLResponse(_VIEWER_HTML)
