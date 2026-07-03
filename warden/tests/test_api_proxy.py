"""API reverse-proxy integration (W14, §8.1): passthrough, merge→403,
MR source-branch namespace, no token leak.
"""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from warden.guards.gitlab_api.catalog import DEFAULT_ENABLED
from warden.guards.gitlab_api.catalog.entries import CATALOG
from warden.guards.gitlab_api.guard import _needs_mr_owner
from warden.guards.gitlab_api.parsing import iid_from_path as _iid_from_path
from warden.guards.gitlab_api.parsing import project_from_path as _project_from_path

PROJ = "group%2Fproj"


# --- path extractors (pure) ----------------------------------------------------
def test_project_from_path_decodes_and_handles_missing_segment():
    assert _project_from_path("/projects/group%2Fproj/repository/tree") == "group/proj"
    assert _project_from_path("/user") == ""  # no project segment


def test_iid_from_path():
    assert _iid_from_path("/projects/x/merge_requests/7/notes") == 7
    assert _iid_from_path("/projects/x/merge_requests") is None


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


async def test_note_on_non_namespace_branch_denied(client, respx_router):
    # MR lookup says the MR's source_branch is outside the allowed namespace —
    # denied regardless of author (§07 Punkt 4).
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "feature/x", "author": {"id": 42}})
    )
    resp = await client.post(f"/api/v4/projects/{PROJ}/merge_requests/7/notes", json={"body": "hi"})
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R3"


async def test_note_on_namespace_mr_allowed_even_with_foreign_author(client, respx_router):
    # A colleague opened this MR from a claude/ branch and delegates the
    # iteration to the agent — allowed, author-independent (§07 Punkt 4).
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "claude/x", "author": {"id": 999}})
    )
    note = respx_router.route(method="POST", url__regex=r".*/merge_requests/7/notes$").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    resp = await client.post(f"/api/v4/projects/{PROJ}/merge_requests/7/notes", json={"body": "hi"})
    assert resp.status_code == 201
    assert note.calls.last.request.headers["private-token"] == "WRITE-TOKEN"


async def test_inline_discussion_on_namespace_mr_allowed(client, respx_router):
    # Inline diff comment (line-level review) on a namespace-branch MR — forwarded
    # regardless of author (§07 Punkt 4).
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "claude/x", "author": {"id": 999}})
    )
    disc = respx_router.route(method="POST", url__regex=r".*/merge_requests/7/discussions$").mock(
        return_value=httpx.Response(201, json={"id": "abc"})
    )
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/merge_requests/7/discussions",
        json={
            "body": "nit",
            "position": {
                "position_type": "text",
                "new_path": "src/main.rs",
                "new_line": 12,
            },
        },
    )
    assert resp.status_code == 201
    assert disc.calls.last.request.headers["private-token"] == "WRITE-TOKEN"


async def test_inline_discussion_non_namespace_denied(client, respx_router):
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "feature/x", "author": {"id": 42}})
    )
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/merge_requests/7/discussions", json={"body": "nit"}
    )
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R3"


async def test_discussion_reply_on_namespace_mr_allowed_even_with_foreign_author(
    client, respx_router
):
    # Reply under an existing discussion thread on a namespace-branch MR opened
    # by someone else — allowed, author-independent (§07 Punkt 4).
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "claude/x", "author": {"id": 999}})
    )
    reply = respx_router.route(
        method="POST", url__regex=r".*/merge_requests/7/discussions/abc123/notes$"
    ).mock(return_value=httpx.Response(201, json={"id": 2}))
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/merge_requests/7/discussions/abc123/notes",
        json={"body": "done"},
    )
    assert resp.status_code == 201
    assert reply.calls.last.request.headers["private-token"] == "WRITE-TOKEN"


async def test_discussion_reply_non_namespace_denied(client, respx_router):
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "feature/x", "author": {"id": 42}})
    )
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/merge_requests/7/discussions/abc123/notes",
        json={"body": "done"},
    )
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R3"


async def test_unknown_write_endpoint_default_denied(client, respx_router):
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/repository/branches", json={"branch": "claude/x"}
    )
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R3"


async def test_project_outside_allowlist_denied(client, respx_router):
    resp = await client.get("/api/v4/projects/other%2Fsecret/repository/tree")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_create_mr_form_encoded_body_is_parsed(client, respx_router):
    # GitLab clients may send form-encoded writes; the source_branch field must
    # still be extracted for the prefix check.
    route = respx_router.route(method="POST", url__regex=r".*/merge_requests$").mock(
        return_value=httpx.Response(201, json={"iid": 9})
    )
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/merge_requests",
        content=b"source_branch=claude/x&target_branch=main",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 201
    assert route.calls.last.request.headers["private-token"] == "WRITE-TOKEN"


async def test_malformed_json_body_is_denied_not_crashed(client, respx_router):
    # Unparsable body → no decision fields extracted → prefix check fails → 403,
    # never a 500 (the parse error is swallowed, default-deny holds).
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/merge_requests",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R2"


# --- B1: projectless read scoping ("content, not visibility") -----------------
@pytest.mark.parametrize(
    "path", ["/projects", "/groups/1/projects", "/user", "/version", "/groups/1"]
)
async def test_projectless_metadata_endpoint_passed_through(client, respx_router, path):
    route = respx_router.route(method="GET", url__regex=r".*").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    resp = await client.get(f"/api/v4{path}")
    assert resp.status_code == 200
    assert route.calls.last.request.headers["private-token"] == "READ-TOKEN"


@pytest.mark.parametrize("scope", ["blobs", "commits", "wiki_blobs", "notes"])
async def test_global_search_content_scope_denied(client, respx_router, scope):
    resp = await client.get(f"/api/v4/search?scope={scope}")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_global_search_without_scope_denied(client, respx_router):
    resp = await client.get("/api/v4/search")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_global_search_unknown_scope_denied(client, respx_router):
    resp = await client.get("/api/v4/search?scope=bogus")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_global_search_metadata_scope_allowed(client, respx_router):
    respx_router.route(method="GET", url__regex=r".*/search.*").mock(
        return_value=httpx.Response(200, json=[{"id": 1}])
    )
    resp = await client.get("/api/v4/search?scope=projects")
    assert resp.status_code == 200


async def test_group_search_content_scope_denied(client, respx_router):
    resp = await client.get("/api/v4/groups/1/search?scope=blobs")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_snippets_denied(client, respx_router):
    resp = await client.get("/api/v4/snippets")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_unknown_projectless_endpoint_denied(client, respx_router):
    resp = await client.get("/api/v4/admin/ci/variables")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


# --- F12: the query string reaches the upstream request, not just the decision -
async def test_query_string_is_forwarded_upstream(client, respx_router):
    route = respx_router.route(method="GET", url__regex=r".*/merge_requests.*").mock(
        return_value=httpx.Response(200, json=[])
    )
    resp = await client.get(f"/api/v4/projects/{PROJ}/merge_requests?state=opened&per_page=50")
    assert resp.status_code == 200
    sent_url = route.calls.last.request.url
    assert sent_url.params["state"] == "opened"
    assert sent_url.params["per_page"] == "50"


async def test_search_scope_query_is_forwarded_when_allowed(client, respx_router):
    # F12 also matters for the allow path: the scope that made the decision
    # pass must be the same scope GitLab receives.
    route = respx_router.route(method="GET", url__regex=r".*/search.*").mock(
        return_value=httpx.Response(200, json=[])
    )
    resp = await client.get("/api/v4/search?scope=issues&search=foo")
    assert resp.status_code == 200
    sent_url = route.calls.last.request.url
    assert sent_url.params["scope"] == "issues"
    assert sent_url.params["search"] == "foo"


# --- B5: GraphQL is a designed dead end, never proxied -------------------------
async def test_graphql_post_denied_no_upstream_call(client, respx_router):
    resp = await client.post("/api/graphql", json={"query": "{ currentUser { id } }"})
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_graphql_get_denied(client, respx_router):
    resp = await client.get("/api/graphql")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_graphql_subpath_denied(client, respx_router):
    resp = await client.post("/api/graphql/whatever")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


# --- audit coverage: every new deny lands in the log ---------------------------
async def _read_audit_lines(ctx, tmp_path, make_request):
    # Redirect the *existing* AuditLog in place (rather than replacing
    # ctx.audit with a new instance): the guards were assembled once, at
    # build_context() time, and each holds its own reference to this exact
    # object (§03.5/03.6) — reassigning ctx.audit itself would not reach them.
    logf = tmp_path / "audit.jsonl"
    ctx.audit._path = str(logf)
    ctx.audit.start()
    await make_request()
    await ctx.audit.stop()
    return [json.loads(line) for line in logf.read_text().splitlines()]


async def test_snippets_deny_is_audited(client, ctx, tmp_path):
    records = await _read_audit_lines(ctx, tmp_path, lambda: client.get("/api/v4/snippets"))
    assert len(records) == 1
    assert records[0]["decision"] == "deny"
    assert records[0]["rule"] == "R6"
    assert records[0]["path"] == "/snippets"


async def test_search_content_scope_deny_is_audited(client, ctx, tmp_path):
    records = await _read_audit_lines(
        ctx, tmp_path, lambda: client.get("/api/v4/search?scope=blobs")
    )
    assert len(records) == 1
    assert records[0]["decision"] == "deny"
    assert records[0]["rule"] == "R6"


async def test_graphql_deny_is_audited(client, ctx, tmp_path):
    records = await _read_audit_lines(
        ctx, tmp_path, lambda: client.post("/api/graphql", json={"query": "{}"})
    )
    assert len(records) == 1
    assert records[0]["decision"] == "deny"
    assert records[0]["rule"] == "R6"
    assert "graphql" in records[0]["path"]


# --- F2: needs-based ownership resolution, not function-identity ---------------


def test_needs_mr_owner_true_for_note_endpoint():
    ep = next(e for e in CATALOG if e.id == "mr.note")
    assert _needs_mr_owner(ep)


def test_needs_mr_owner_false_for_mr_create():
    ep = next(e for e in CATALOG if e.id == "mr.create")
    assert not _needs_mr_owner(ep)


def test_needs_mr_owner_false_for_entry_with_no_checks():
    ep = next(e for e in CATALOG if e.id == "issue.create")
    assert not _needs_mr_owner(ep)


# --- F12: decision fields are read only from their declared location -----------


async def test_body_field_sent_only_as_query_is_not_used_for_the_decision(client, respx_router):
    # source_branch is a BODY-declared decision field for mr.create (§04.2).
    # Sending it only as a query parameter must not satisfy field_has_prefix —
    # the field must be treated as simply absent, not silently read from the
    # wrong location (F12's actual footgun: a scoping check "passing" on a
    # value the checked location never carried).
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/merge_requests?source_branch=claude/x",
        json={"target_branch": "main"},
    )
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R2"


# --- §04.3: audit marks non-default-activated catalog entries ------------------


def _activated_client_ctx(cfg, respx_router):
    """Build a fresh app/ctx/client with branch.create activated beyond the
    default set — the shared ``client``/``ctx`` fixtures use the plain
    default activation, so this scenario needs its own wiring.
    """
    from warden.app import create_app
    from warden.context import build_context
    from warden.core.audit import AuditLog
    from warden.core.state import State

    activated_cfg = replace(
        cfg,
        endpoint_enable=tuple(DEFAULT_ENABLED) + ("branch.create",),
    )
    state = State(":memory:")
    state.mark_reconciled()
    ctx = build_context(activated_cfg, state, AuditLog("-"))
    return ctx, create_app(ctx)


async def test_config_activated_entry_is_reachable_and_forwards(cfg, respx_router):
    ctx, app = _activated_client_ctx(cfg, respx_router)
    transport = httpx.ASGITransport(app=app)
    route = respx_router.route(method="POST", url__regex=r".*/repository/branches$").mock(
        return_value=httpx.Response(201, json={"name": "claude/x"})
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://warden") as c:
        resp = await c.post(
            f"/api/v4/projects/{PROJ}/repository/branches",
            json={"branch": "claude/x", "ref": "claude/y"},
        )
    assert resp.status_code == 201
    assert route.calls.last.request.headers["private-token"] == "WRITE-TOKEN"
    await ctx.upstream.aclose()


async def test_config_activated_entry_wrong_prefix_still_denied(cfg, respx_router):
    ctx, app = _activated_client_ctx(cfg, respx_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://warden") as c:
        resp = await c.post(
            f"/api/v4/projects/{PROJ}/repository/branches",
            json={"branch": "main", "ref": "main"},
        )
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R2"
    await ctx.upstream.aclose()


async def test_config_activated_entry_marked_in_audit_default_entry_is_not(
    cfg, respx_router, tmp_path
):
    ctx, app = _activated_client_ctx(cfg, respx_router)
    # Redirect the existing AuditLog in place — the guards inside `app` were
    # already assembled (in _activated_client_ctx) around this exact object.
    logf = tmp_path / "audit.jsonl"
    ctx.audit._path = str(logf)
    ctx.audit.start()
    respx_router.route(method="POST", url__regex=r".*/repository/branches$").mock(
        return_value=httpx.Response(201, json={"name": "claude/x"})
    )
    respx_router.route(method="POST", url__regex=r".*/merge_requests$").mock(
        return_value=httpx.Response(201, json={"iid": 1})
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://warden") as c:
        await c.post(
            f"/api/v4/projects/{PROJ}/repository/branches",
            json={"branch": "claude/x", "ref": "claude/y"},
        )
        await c.post(
            f"/api/v4/projects/{PROJ}/merge_requests",
            json={"source_branch": "claude/x", "target_branch": "main"},
        )
    await ctx.audit.stop()
    records = [json.loads(line) for line in logf.read_text().splitlines()]
    branch_event = next(r for r in records if r["path"].endswith("/repository/branches"))
    mr_event = next(r for r in records if r["path"].endswith("/merge_requests"))
    assert branch_event["enabled_via"] == "config:branch.create"
    assert "enabled_via" not in mr_event  # default-activated entry: no marking
    await ctx.upstream.aclose()
