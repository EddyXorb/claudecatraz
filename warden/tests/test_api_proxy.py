"""API reverse-proxy integration (W14, §8.1): passthrough, merge→403, ownership, no token leak."""

from __future__ import annotations

import httpx
import pytest

PROJ = "group%2Fproj"


async def test_get_is_passed_through_with_read_token(client, respx_router):
    route = respx_router.route(method="GET", url__regex=r".*/repository/tree.*").mock(
        return_value=httpx.Response(200, json=[{"name": "README.md"}])
    )
    resp = await client.get(f"/api/v4/projects/{PROJ}/repository/tree")
    assert resp.status_code == 200
    assert resp.json() == [{"name": "README.md"}]
    # Read-token injected upstream …
    assert route.calls.last.request.headers["private-token"] == "READ-TOKEN"
    # … and never leaked back to the agent.
    assert "private-token" not in {k.lower() for k in resp.headers}
    assert "authorization" not in {k.lower() for k in resp.headers}


async def test_merge_endpoint_is_always_403(client, respx_router):
    resp = await client.put(f"/api/v4/projects/{PROJ}/merge_requests/7/merge")
    assert resp.status_code == 403
    body = resp.json()
    assert body["rule"] == "R4"
    # No upstream call happened (respx would raise on an unmocked request).


async def test_create_mr_wrong_prefix_denied(client, respx_router):
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/merge_requests",
        json={"source_branch": "feature/x", "target_branch": "main"},
    )
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R2"


async def test_create_mr_with_prefix_forwarded_with_write_token(client, respx_router):
    route = respx_router.route(method="POST", url__regex=r".*/merge_requests$").mock(
        return_value=httpx.Response(201, json={"iid": 9})
    )
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/merge_requests",
        json={"source_branch": "claude/x", "target_branch": "main"},
    )
    assert resp.status_code == 201
    assert route.calls.last.request.headers["private-token"] == "WRITE-TOKEN"


async def test_note_ownership_violation_denied(client, respx_router):
    # MR lookup says the MR belongs to a different author.
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(
            200, json={"source_branch": "claude/x", "author": {"id": 999}}
        )
    )
    resp = await client.post(f"/api/v4/projects/{PROJ}/merge_requests/7/notes", json={"body": "hi"})
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R3"


async def test_note_on_owned_mr_allowed(client, respx_router):
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(
            200, json={"source_branch": "claude/x", "author": {"id": 42}}
        )
    )
    note = respx_router.route(method="POST", url__regex=r".*/merge_requests/7/notes$").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    resp = await client.post(f"/api/v4/projects/{PROJ}/merge_requests/7/notes", json={"body": "hi"})
    assert resp.status_code == 201
    assert note.calls.last.request.headers["private-token"] == "WRITE-TOKEN"


async def test_unknown_write_endpoint_default_denied(client, respx_router):
    resp = await client.post(f"/api/v4/projects/{PROJ}/repository/branches", json={"branch": "claude/x"})
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R3"


async def test_project_outside_allowlist_denied(client, respx_router):
    resp = await client.get("/api/v4/projects/other%2Fsecret/repository/tree")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"
