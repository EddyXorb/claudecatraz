"""Red-team bypass attempts (W14, §8.2) at the HTTP boundary — the cheap, no-docker
subset: merge aliases, prefix tricks, cross-project reads, API branch creation.

The full hostile-agent docker-compose suite (§8.2: printenv has no token, no direct
connect, flooding, exfil) is environment-level and lives outside this unit suite.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from warden.pktline import FLUSH, pkt_line

PROJ = "group%2Fproj"
ZERO = "0" * 40
SHA1 = "1" * 40


async def test_merge_alias_when_pipeline_succeeds_blocked(client, respx_router):
    resp = await client.put(
        f"/api/v4/projects/{PROJ}/merge_requests/7/merge?merge_when_pipeline_succeeds=true"
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R4"


async def test_merge_via_state_event_blocked(client, respx_router):
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "claude/x", "author": {"id": 42}})
    )
    resp = await client.put(
        f"/api/v4/projects/{PROJ}/merge_requests/7", json={"state_event": "merge"}
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R4"


async def test_cross_project_read_blocked(client, respx_router):
    resp = await client.get("/api/v4/projects/secret%2Fdata/repository/files/x")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_api_branch_creation_default_denied(client, respx_router):
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/repository/branches",
        json={"branch": "claude/x", "ref": "main"},
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R3"


async def test_push_prefix_lookalike_blocked(client, respx_router):
    # "claudex/feature" shares the leading "claude" but misses the slash
    # separator, so it must NOT satisfy the "claude/" prefix.
    body = (
        pkt_line(f"{ZERO} {SHA1} refs/heads/claudex/feature\x00report-status\n".encode())
        + FLUSH
        + b"PACK"
    )
    resp = await client.post("/git/group/proj.git/git-receive-pack", content=body)
    assert b"warden: R2" in resp.content


async def test_git_cross_project_push_blocked(client, respx_router):
    body = pkt_line(f"{ZERO} {SHA1} refs/heads/claude/x\x00report-status\n".encode()) + FLUSH + b"PACK"
    resp = await client.post("/git/other/secret.git/git-receive-pack", content=body)
    assert b"warden: R6" in resp.content
