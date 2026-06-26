"""Uniform deny / git-reject responses (W13).

API deny → 403 JSON, never leaking a GitLab response. git reject → a correctly
framed ``report-status`` over the side-band so ``git push`` shows a clear
``! [remote rejected] … (warden: R2 …)``.
"""

from __future__ import annotations

from starlette.responses import JSONResponse, Response

from .pktline import FLUSH, pkt_line
from .policy import Decision

GIT_RECEIVE_RESULT = "application/x-git-receive-pack-result"


def deny_json(decision: Decision, status: int = 403) -> JSONResponse:
    return JSONResponse(
        {"error": "forbidden", "rule": decision.rule, "reason": decision.reason},
        status_code=status,
    )


def git_reject_body(decisions: list[Decision], refs: list[str], *, sideband: bool) -> bytes:
    """Build a `report-status` payload rejecting every ref with the deny reason."""
    inner = pkt_line(b"unpack ok\n")
    for ref, d in zip(refs, decisions):
        reason = f"warden: {d.rule} {d.reason}"
        inner += pkt_line(f"ng {ref} {reason}\n".encode())
    inner += FLUSH
    if sideband:
        # Multiplex the whole report onto data channel 1, then an outer flush.
        return pkt_line(b"\x01" + inner) + FLUSH
    return inner


def git_reject_response(
    decisions: list[Decision], refs: list[str], *, sideband: bool
) -> Response:
    return Response(
        content=git_reject_body(decisions, refs, sideband=sideband),
        media_type=GIT_RECEIVE_RESULT,
        status_code=200,  # HTTP 200; the rejection is in-band (git convention)
    )
