"""git Smart-HTTP proxy integration: advertise/upload passthrough, push accept/reject."""

from __future__ import annotations

import base64
import json
from dataclasses import replace

import httpx

from warden.app import create_app
from warden.context import build_context
from warden.core.audit import AuditLog
from warden.core.config import Config, GitEndpoint, HostCredentials
from warden.core.state import State
from warden.guards.git.actions import REPO_BRANCH_PUSH, REPO_READ
from warden.guards.git.pktline import FLUSH, pkt_line

ZERO = "0" * 40
SHA1 = "1" * 40
CAPS = "report-status side-band-64k atomic"
RECV = "/git/group/proj.git/git-receive-pack"


def make_push(commands, pack=b"PACK\x00\x00\x00\x02binarypackdata"):
    out = b""
    for idx, (old, new, ref) in enumerate(commands):
        payload = f"{old} {new} {ref}"
        if idx == 0:
            payload += "\x00" + CAPS
        payload += "\n"
        out += pkt_line(payload.encode())
    return out + FLUSH + pack


def _basic(token: str) -> str:
    return "Basic " + base64.b64encode(f"oauth2:{token}".encode()).decode()


async def test_advertise_passthrough_read_token(client, respx_router):
    route = respx_router.route(method="GET", url__regex=r".*/info/refs.*").mock(
        return_value=httpx.Response(200, content=b"001e# service=git-upload-pack\n")
    )
    resp = await client.get("/git/group/proj.git/info/refs?service=git-upload-pack")
    assert resp.status_code == 200
    assert route.calls.last.request.headers["authorization"] == _basic("READ-TOKEN")


async def test_upload_pack_passthrough(client, respx_router):
    route = respx_router.route(method="POST", url__regex=r".*/git-upload-pack$").mock(
        return_value=httpx.Response(200, content=b"fetched-pack")
    )
    resp = await client.post("/git/group/proj.git/git-upload-pack", content=b"0032want ...")
    assert resp.status_code == 200
    assert resp.content == b"fetched-pack"
    assert route.calls.last.request.headers["authorization"] == _basic("READ-TOKEN")


async def test_push_wrong_prefix_rejected_without_upstream(client, respx_router):
    body = make_push([(ZERO, SHA1, "refs/heads/main")])
    resp = await client.post(RECV, content=body)
    assert resp.status_code == 200  # rejection is in-band
    assert b"ng refs/heads/main" in resp.content
    assert b"warden: R2" in resp.content


async def test_push_prefixed_branch_streamed_sha_preserving(client, respx_router, ctx):
    body = make_push([(ZERO, SHA1, "refs/heads/claude/feature")])
    route = respx_router.route(method="POST", url__regex=r".*/git-receive-pack$").mock(
        return_value=httpx.Response(
            200, content=pkt_line(b"\x01" + pkt_line(b"unpack ok\n") + FLUSH)
        )
    )
    resp = await client.post(RECV, content=body)
    assert resp.status_code == 200
    sent = route.calls.last.request
    # Write-token injected, and the body forwarded byte-for-byte (SHA-preserving).
    assert sent.headers["authorization"] == _basic("WRITE-TOKEN")
    assert sent.content == body
    # The create was recorded for the branch quota (the git guard's own
    # BranchState, not a shared forge_state).
    git_guard = next(g for g in ctx.guards if g.name == "git")
    assert git_guard.branch_state.open_branches("gitlab.example") == 1
    assert ctx.state.writes_last_hour("gitlab.example") == 1
    # Regression: the project key is normalised (no ``.git`` suffix) so it matches
    # the reconcile/allowlist form. Otherwise the push row (``proj.git``) and the
    # reconcile row (``proj``) coexist → the branch is counted twice and the push
    # row is never pruned (R5 ``max_open_branches`` drift).
    keys = [r["project"] for r in ctx.state.store.execute("SELECT project FROM agent_branches")]
    assert keys == ["group/proj"]


async def test_push_delete_rejected(client, respx_router):
    # Branch delete is an irreversible verb (M4) — reported as R4, not R2.
    body = make_push([(SHA1, ZERO, "refs/heads/claude/feature")])
    resp = await client.post(RECV, content=body)
    assert b"ng refs/heads/claude/feature" in resp.content
    assert b"warden: R4" in resp.content


async def test_advertise_denied_for_project_outside_allowlist(client, respx_router):
    # A non-allowlisted project must not even be discoverable over git (R6).
    resp = await client.get("/git/other/secret.git/info/refs?service=git-upload-pack")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_upload_pack_denied_for_project_outside_allowlist(client, respx_router):
    resp = await client.post("/git/other/secret.git/git-upload-pack", content=b"0032want ...")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_push_forwards_content_encoding(client, respx_router):
    # gzip stays gzip: the Content-Encoding header is passed upstream untouched.
    body = make_push([(ZERO, SHA1, "refs/heads/claude/feature")])
    route = respx_router.route(method="POST", url__regex=r".*/git-receive-pack$").mock(
        return_value=httpx.Response(
            200, content=pkt_line(b"\x01" + pkt_line(b"unpack ok\n") + FLUSH)
        )
    )
    resp = await client.post(RECV, content=body, headers={"content-encoding": "gzip"})
    assert resp.status_code == 200
    assert route.calls.last.request.headers.get("content-encoding") == "gzip"


# --- push-size limit: cheap Content-Length gate, no packfile parsing ---


def _client_with(cfg: Config) -> httpx.AsyncClient:
    """Build an ASGI test client for a warden with a specific ``cfg`` (max_push_bytes here)."""
    state = State(":memory:")
    state.mark_reconciled("git")
    state.mark_reconciled("api")
    ctx = build_context(cfg, state, AuditLog("-"))
    transport = httpx.ASGITransport(app=create_app(ctx))
    return httpx.AsyncClient(transport=transport, base_url="http://gitlab.example")


async def test_push_over_max_push_bytes_rejected_r5(cfg, respx_router):
    small_cfg = replace(cfg, max_push_bytes=200)
    async with _client_with(small_cfg) as c:
        body = make_push([(ZERO, SHA1, "refs/heads/claude/feature")], pack=b"PACK" + b"\x00" * 500)
        assert len(body) > 200
        resp = await c.post(RECV, content=body)
    assert resp.status_code == 200  # in-band rejection
    assert b"warden: R5" in resp.content
    assert b"exceeds max_push_bytes" in resp.content


async def test_push_under_max_push_bytes_is_forwarded(cfg, respx_router):
    small_cfg = replace(cfg, max_push_bytes=10_000)
    route = respx_router.route(method="POST", url__regex=r".*/git-receive-pack$").mock(
        return_value=httpx.Response(
            200, content=pkt_line(b"\x01" + pkt_line(b"unpack ok\n") + FLUSH)
        )
    )
    async with _client_with(small_cfg) as c:
        body = make_push([(ZERO, SHA1, "refs/heads/claude/feature")])
        assert len(body) < 10_000
        resp = await c.post(RECV, content=body)
    assert resp.status_code == 200
    assert route.called


# --- per-host access-mode gate tests -------------------------------------------
# Build a fresh test client per access mode; no upstream mock needed (a hit would fail).

UPSTREAM = "https://gitlab.example"


def _access_client(access: str) -> httpx.AsyncClient:
    """Build an ASGI test client for a warden whose one `[[git.endpoint]]`
    resolves to the given access mode (``closed``/``read-only``/``read-write``,
    :meth:`~warden.core.config.Config.access_mode`) — derived from which of
    its tokens are present; there is no declared mode."""
    if access == "closed":
        creds: dict[str, HostCredentials] = {}
    elif access == "read-only":
        creds = {"gitlab.example": HostCredentials(read_token="READ-TOKEN")}
    else:
        creds = {
            "gitlab.example": HostCredentials(read_token="READ-TOKEN", write_token="WRITE-TOKEN")
        }
    cfg = Config(
        branch_prefixes=("claude/",),
        allowed_projects=("group/proj",),
        state_db_path=":memory:",
        git_endpoints=(GitEndpoint(host="gitlab.example", type="gitlab"),),
        git_credentials=creds,
    )
    state = State(":memory:")
    state.mark_reconciled("git")
    state.mark_reconciled("api")
    audit = AuditLog("-")
    ctx = build_context(cfg, state, audit)
    app = create_app(ctx)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://gitlab.example")


async def test_closed_advertise_clone_denied_no_upstream():
    """A closed host: git clone discovery (git-upload-pack) is denied by
    `host_gate` (R6), before any mode/write check; no upstream call."""
    async with _access_client("closed") as c:
        resp = await c.get("/git/group/proj.git/info/refs?service=git-upload-pack")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_closed_upload_pack_denied_no_upstream():
    """A closed host: git fetch body (upload-pack) is denied R6; no upstream call."""
    async with _access_client("closed") as c:
        resp = await c.post("/git/group/proj.git/git-upload-pack", content=b"0032want ...")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R6"


async def test_read_only_advertise_receive_pack_denied_no_upstream():
    """A read-only host: push discovery (git-receive-pack) is denied R0
    before git_get."""
    async with _access_client("read-only") as c:
        resp = await c.get("/git/group/proj.git/info/refs?service=git-receive-pack")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R0"
    assert "read-only" in resp.json()["reason"]


async def test_read_only_push_discovery_denied_r0_not_r6_for_unallowed_project():
    # The kernel checks host -> writes -> project in that order, so a
    # write-mode denial (R0) preempts the allowlist check here rather than
    # reporting R6.
    async with _access_client("read-only") as c:
        resp = await c.get("/git/other/secret.git/info/refs?service=git-receive-pack")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R0"


async def _read_audit_lines(ctx, tmp_path, make_request):
    # Redirect the *existing* AuditLog in place (rather than replacing
    # ctx.audit with a new instance): the guards were assembled once, at
    # build_context() time, and each holds its own reference to this exact
    # object — reassigning ctx.audit itself would not reach them.
    logf = tmp_path / "audit.jsonl"
    ctx.audit._path = str(logf)
    ctx.audit.start()
    await make_request()
    await ctx.audit.stop()
    return [json.loads(line) for line in logf.read_text().splitlines()]


async def test_advertise_read_is_audited(client, respx_router, ctx, tmp_path):
    # Git reads run through the same kernel pipeline as pushes/API reads, so
    # they show up in the audit log too.
    respx_router.route(method="GET", url__regex=r".*/info/refs.*").mock(
        return_value=httpx.Response(200, content=b"001e# service=git-upload-pack\n")
    )
    records = await _read_audit_lines(
        ctx,
        tmp_path,
        lambda: client.get("/git/group/proj.git/info/refs?service=git-upload-pack"),
    )
    assert len(records) == 1
    assert records[0]["guard"] == "git"
    assert records[0]["decision"] == "allow"


async def test_upload_pack_read_is_audited(client, respx_router, ctx, tmp_path):
    respx_router.route(method="POST", url__regex=r".*/git-upload-pack$").mock(
        return_value=httpx.Response(200, content=b"fetched-pack")
    )
    records = await _read_audit_lines(
        ctx,
        tmp_path,
        lambda: client.post("/git/group/proj.git/git-upload-pack", content=b"0032want ..."),
    )
    assert len(records) == 1
    assert records[0]["guard"] == "git"
    assert records[0]["decision"] == "allow"


async def test_read_only_advertise_upload_pack_passes_through(respx_router):
    """A read-only host: clone discovery (git-upload-pack) still passes through with READ."""
    import respx as respx_module

    with respx_module.mock(base_url=UPSTREAM, assert_all_called=False) as router:
        route = router.route(method="GET", url__regex=r".*/info/refs.*").mock(
            return_value=httpx.Response(200, content=b"001e# service=git-upload-pack\n")
        )
        async with _access_client("read-only") as c:
            resp = await c.get("/git/group/proj.git/info/refs?service=git-upload-pack")

    assert resp.status_code == 200
    assert route.call_count == 1
    # read token is used (not write)
    auth = route.calls.last.request.headers["authorization"]
    assert "READ-TOKEN" in base64.b64decode(auth.split(" ", 1)[1]).decode()


# --- action gate: repo.read/repo.branch.push per host ---------------------------
# `capability_gate` consults `policy.action_gate` for all three operations
# (advertise/upload-pack/receive-pack), so a `repo.branch.push`-disabled host
# is denied already at push *discovery* — before the client sends the pack.


def _client_with_actions(actions: tuple[str, ...]) -> httpx.AsyncClient:
    cfg = Config(
        branch_prefixes=("claude/",),
        allowed_projects=("group/proj",),
        state_db_path=":memory:",
        git_endpoints=(GitEndpoint(host="gitlab.example", type="gitlab", actions=actions),),
        git_credentials={
            "gitlab.example": HostCredentials(read_token="READ-TOKEN", write_token="WRITE-TOKEN")
        },
    )
    return _client_with(cfg)


async def test_action_gate_denies_push_advertise_when_git_push_not_enabled(respx_router):
    # Push discovery (advertise ?service=git-receive-pack) denied BEFORE any
    # pack is sent; no upstream route is registered at all here, so an
    # accidental fall-through to `forward` would fail loudly (respx raises).
    async with _client_with_actions((REPO_READ.id,)) as c:
        resp = await c.get("/git/group/proj.git/info/refs?service=git-receive-pack")
    assert resp.status_code == 403
    body = resp.json()
    assert body["rule"] == "R6"
    assert REPO_BRANCH_PUSH.id in body["reason"]


async def test_action_gate_denies_receive_pack_when_git_push_not_enabled(respx_router):
    async with _client_with_actions((REPO_READ.id,)) as c:
        body = make_push([(ZERO, SHA1, "refs/heads/claude/feature")])
        resp = await c.post(RECV, content=body)
    assert resp.status_code == 200  # in-band rejection, git convention
    assert b"warden: R6" in resp.content
    assert REPO_BRANCH_PUSH.id.encode() in resp.content


async def test_action_gate_allows_fetch_advertise_and_upload_pack_when_only_git_fetch_enabled(
    respx_router,
):
    respx_router.route(method="GET", url__regex=r".*/info/refs.*").mock(
        return_value=httpx.Response(200, content=b"001e# service=git-upload-pack\n")
    )
    respx_router.route(method="POST", url__regex=r".*/git-upload-pack$").mock(
        return_value=httpx.Response(200, content=b"fetched-pack")
    )
    async with _client_with_actions((REPO_READ.id,)) as c:
        advertise = await c.get("/git/group/proj.git/info/refs?service=git-upload-pack")
        upload = await c.post("/git/group/proj.git/git-upload-pack", content=b"0032want ...")
    assert advertise.status_code == 200
    assert upload.status_code == 200
    assert upload.content == b"fetched-pack"


async def test_action_gate_full_default_allows_fetch_and_push(respx_router):
    # Host with both actions enabled: fetch *and* push discovery both pass
    # the action gate (they still go through the usual R0/R2/etc. checks
    # afterwards — this only asserts the action gate itself doesn't deny).
    respx_router.route(method="GET", url__regex=r".*/info/refs.*").mock(
        return_value=httpx.Response(200, content=b"001e# service=git-upload-pack\n")
    )
    async with _client_with_actions((REPO_READ.id, REPO_BRANCH_PUSH.id)) as c:
        fetch_advertise = await c.get("/git/group/proj.git/info/refs?service=git-upload-pack")
        push_advertise = await c.get("/git/group/proj.git/info/refs?service=git-receive-pack")
    assert fetch_advertise.status_code == 200
    assert push_advertise.status_code == 200  # push discovery passes; R1
