"""Uniform API deny response: 403 JSON, never leaking a GitLab response."""

from __future__ import annotations

from starlette.responses import JSONResponse

from .core.model import Decision


def deny_json(decision: Decision, status: int = 403) -> JSONResponse:
    return JSONResponse(
        {"error": "forbidden", "reason": decision.reason},
        status_code=status,
    )
