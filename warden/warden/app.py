"""Starlette app + routing: agent port vs. admin port.

Generic assembly only: stays at the top of the package, free of guard internals.
Each guard in ``ctx.guards`` supplies its own routes (:meth:`~warden.core.guard.Guard.routes`);
this module never imports a concrete guard class, only :mod:`warden.context`'s guard-agnostic
:class:`~warden.context.AppContext`. The pipeline every route runs through lives in :mod:`warden.core.guard`.
"""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .context import AppContext
from .guards.gitlab_api.catalog import endpoint_table_report

# Static log-viewer page (O.4) — a package asset, not routing code (F7).
_VIEWER_HTML_PATH = Path(__file__).parent / "static" / "viewer.html"
_VIEWER_HTML = _VIEWER_HTML_PATH.read_text(encoding="utf-8")


def create_app(ctx: AppContext) -> Starlette:
    """Agent-facing app on port 8080: API proxy + git Smart-HTTP.

    Generic assembly: every guard supplies its own routes
    (:meth:`~warden.core.guard.Guard.routes`); this module never lists
    guard endpoints, staying free of guard-policy internals.
    """
    # The /git/ prefix is load-bearing: it separates git Smart-HTTP routes from
    # /api/v4/… on the same port. Repos keep their canonical remote URL
    # (https://gitlab.com/…); the entrypoint injects a global git insteadOf rewrite
    # (https://gitlab.com/ → http://gitlab-warden:8080/git/) so the prefix is added
    # transparently at transport time without touching .git/config. See W3.1.
    routes = [r for g in ctx.guards for r in g.routes()]
    routes.append(Route("/healthz", _healthz, methods=["GET"]))
    app = Starlette(routes=routes)
    app.state.ctx = ctx
    return app


def create_admin_app(ctx: AppContext) -> Starlette:
    """Admin app on port 9090: healthz + read-only log tail + viewer."""
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
            "service_account_id": ctx.forge.service_account_id,
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
    """Read-only summary of the effective endpoint catalog (admin net only).

    Every entry: whether part of the default set, whether activated.
    ``catraz doctor``/``catraz allow-endpoint`` use this to show catalog ids and state.
    """
    ctx: AppContext = request.app.state.ctx
    return JSONResponse(endpoint_table_report(ctx.cfg))


async def _viewer(request: Request) -> HTMLResponse:
    """Static log viewer (O.4): filters JSONL by guard/rule/decision/project."""
    return HTMLResponse(_VIEWER_HTML)
