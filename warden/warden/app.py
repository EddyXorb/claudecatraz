"""Starlette app + routing: API vs. git, agent port vs. admin port (W3, W4)."""

from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import api_proxy, git_proxy
from .context import AppContext


def create_app(ctx: AppContext) -> Starlette:
    """Agent-facing app on port 8080: API proxy + git Smart-HTTP (W4)."""
    routes = [
        Route("/git/{project:path}/info/refs", git_proxy.advertise, methods=["GET"]),
        Route("/git/{project:path}/git-upload-pack", git_proxy.upload_pack, methods=["POST"]),
        Route("/git/{project:path}/git-receive-pack", git_proxy.receive_pack, methods=["POST"]),
        Route(
            "/api/v4/{rest:path}",
            api_proxy.handle,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
        ),
        Route("/healthz", _healthz, methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    app.state.ctx = ctx
    return app


def create_admin_app(ctx: AppContext) -> Starlette:
    """Admin app on port 9090: healthz + read-only log tail (W3, §6.8)."""
    routes = [
        Route("/healthz", _healthz, methods=["GET"]),
        Route("/audit", _audit_tail, methods=["GET"]),
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
