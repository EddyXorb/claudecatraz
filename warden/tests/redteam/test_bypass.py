"""Red-team bypass attempts (W14, §8.2) at the HTTP boundary — the cheap, no-docker
subset: merge aliases, prefix tricks, cross-project reads, API branch creation.

The full hostile-agent docker-compose suite (§8.2: printenv has no token, no direct
connect, flooding, exfil) is environment-level and lives outside this unit suite.
"""

from __future__ import annotations

import httpx

from warden.guards.git.pktline import FLUSH, pkt_line

PROJ = "group%2Fproj"
ZERO = "0" * 40
SHA1 = "1" * 40


async def test_merge_alias_when_pipeline_succeeds_blocked(client, respx_router):
    resp = await client.put(
        f"/api/v4/projects/{PROJ}/merge_requests/7/merge?merge_when_pipeline_succeeds=true"
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R4"


async def test_merge_via_state_event_blocked(client, respx_router):
    # Foreign author but namespace source_branch — §07 Punkt 4 allows touching the MR,
    # but R4's merge block is independent and must still apply.
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "claude/x", "author": {"id": 999}})
    )
    resp = await client.put(
        f"/api/v4/projects/{PROJ}/merge_requests/7", json={"state_event": "merge"}
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R4"


async def test_mr_note_on_non_namespace_branch_still_denied(client, respx_router):
    # The security boundary that matters (§07 Punkt 4): dropping the author check
    # must NOT also drop the namespace check. A MR whose source_branch is outside
    # the allowed prefixes stays blocked no matter who authored it.
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "feature/x", "author": {"id": 42}})
    )
    resp = await client.post(f"/api/v4/projects/{PROJ}/merge_requests/7/notes", json={"body": "hi"})
    assert resp.status_code == 403 and resp.json()["rule"] == "R3"


async def test_cross_project_read_blocked(client, respx_router):
    resp = await client.get("/api/v4/projects/secret%2Fdata/repository/files/x")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


# --- B1: "content, not visibility" — projectless content-capable reads must
# stay blocked no matter how they're dressed up, even though the token can
# technically see every project. ---
async def test_global_blob_search_cannot_harvest_code_across_projects(client, respx_router):
    # The whole point of B1: global search with scope=blobs returns code content
    # from *every* project visible to the token, bypassing allowed_projects.
    resp = await client.get("/api/v4/search?scope=blobs&search=password")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_group_commit_search_cannot_harvest_content(client, respx_router):
    resp = await client.get("/api/v4/groups/1/search?scope=commits&search=secret")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_snippets_cannot_be_read_projectless(client, respx_router):
    resp = await client.get("/api/v4/snippets")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_group_discovery_still_works_despite_b1(client, respx_router):
    # B1 must not regress the AGENT.md-documented discovery flow: names/metadata
    # stay readable, only content is scoped.
    respx_router.route(method="GET", url__regex=r".*/groups/.*/projects$").mock(
        return_value=httpx.Response(200, json=[{"name": "proj"}])
    )
    resp = await client.get("/api/v4/groups/1/projects")
    assert resp.status_code == 200


# --- B5: GraphQL must never become a policy bypass -----------------------------
async def test_graphql_mutation_cannot_bypass_merge_block(client, respx_router):
    # A GraphQL mutation could merge an MR in one call, bypassing R4 entirely —
    # the warden must refuse before any upstream contact, not rely on GitLab.
    resp = await client.post(
        "/api/graphql",
        json={"query": "mutation { mergeRequestAccept(input: {}) { errors } }"},
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_graphql_read_query_also_denied(client, respx_router):
    resp = await client.get("/api/graphql")
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
    body = (
        pkt_line(f"{ZERO} {SHA1} refs/heads/claude/x\x00report-status\n".encode()) + FLUSH + b"PACK"
    )
    resp = await client.post("/git/other/secret.git/git-receive-pack", content=body)
    assert b"warden: R6" in resp.content
