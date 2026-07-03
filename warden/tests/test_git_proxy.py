"""git Smart-HTTP proxy integration (W14): advertise/upload passthrough, push accept/reject."""

from __future__ import annotations

import base64

import httpx

from warden.app import create_app
from warden.core.audit import AuditLog
from warden.core.config import Config
from warden.core.state import State
from warden.guards.git.pktline import FLUSH, pkt_line
from warden.guards.gitlab_api.context import AppContext
from warden.guards.gitlab_api.upstream import Upstream

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
        return_value=httpx.Response(200, content=pkt_line(b"\x01" + pkt_line(b"unpack ok\n") + FLUSH))
    )
    resp = await client.post(RECV, content=body)
    assert resp.status_code == 200
    sent = route.calls.last.request
    # Write-token injected, and the body forwarded byte-for-byte (SHA-preserving).
    assert sent.headers["authorization"] == _basic("WRITE-TOKEN")
    assert sent.content == body
    # The create was recorded for the branch quota.
    assert ctx.state.open_branches() == 1
    assert ctx.state.writes_last_hour() == 1
    # Regression: the project key is normalised (no ``.git`` suffix) so it matches
    # the reconcile/allowlist form. Otherwise the push row (``proj.git``) and the
    # reconcile row (``proj``) coexist → the branch is counted twice and the push
    # row is never pruned (R5 ``max_open_branches`` drift).
    keys = [r["project"] for r in ctx.state._db.execute("SELECT project FROM claude_branches")]
    assert keys == ["group/proj"]


async def test_push_delete_rejected(client, respx_router):
    # B3 fix: branch delete is an irreversible verb (M4) — reported as R4, not R2.
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
    # gzip stays gzip: the Content-Encoding header is passed upstream untouched (W7.4).
    body = make_push([(ZERO, SHA1, "refs/heads/claude/feature")])
    route = respx_router.route(method="POST", url__regex=r".*/git-receive-pack$").mock(
        return_value=httpx.Response(200, content=pkt_line(b"\x01" + pkt_line(b"unpack ok\n") + FLUSH))
    )
    resp = await client.post(RECV, content=body, headers={"content-encoding": "gzip"})
    assert resp.status_code == 200
    assert route.calls.last.request.headers.get("content-encoding") == "gzip"


# --- GITLAB_MODE gate tests (mode-enforcement, step 9) -------------------------
# Build a fresh test client per mode; no upstream mock needed (a hit would fail).

UPSTREAM = "https://gitlab.example"


def _mode_client(mode: str) -> httpx.AsyncClient:
    """Build an ASGI test client for a warden with the given GITLAB_MODE."""
    cfg = Config(
        branch_prefixes=("claude/",),
        allowed_projects=("group/proj",),
        api_url=f"{UPSTREAM}/api/v4",
        read_token="READ-TOKEN",
        write_token="WRITE-TOKEN",
        state_db_path=":memory:",
        gitlab_mode=mode,
    )
    state = State(":memory:")
    state.mark_reconciled()
    upstream = Upstream(cfg)
    audit = AuditLog("-")
    ctx = AppContext(cfg, upstream, state, audit)
    ctx.service_account_id = 42
    app = create_app(ctx)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://warden")


async def test_off_advertise_clone_denied_no_upstream():
    """GITLAB_MODE=off: git clone discovery (git-upload-pack) is denied R0; no upstream call."""
    async with _mode_client("off") as c:
        resp = await c.get("/git/group/proj.git/info/refs?service=git-upload-pack")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R0"
    assert "off" in resp.json()["reason"]


async def test_off_upload_pack_denied_no_upstream():
    """GITLAB_MODE=off: git fetch body (upload-pack) is denied R0; no upstream call."""
    async with _mode_client("off") as c:
        resp = await c.post("/git/group/proj.git/git-upload-pack", content=b"0032want ...")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R0"


async def test_read_only_advertise_receive_pack_denied_no_upstream():
    """GITLAB_MODE=read-only: push discovery (git-receive-pack) is denied R0 before git_get."""
    async with _mode_client("read-only") as c:
        resp = await c.get("/git/group/proj.git/info/refs?service=git-receive-pack")
    assert resp.status_code == 403
    assert resp.json()["rule"] == "R0"
    assert "read-only" in resp.json()["reason"]


async def test_read_only_advertise_upload_pack_passes_through(respx_router):
    """GITLAB_MODE=read-only: clone discovery (git-upload-pack) still passes through with READ."""
    import respx as respx_module

    with respx_module.mock(base_url=UPSTREAM, assert_all_called=False) as router:
        route = router.route(method="GET", url__regex=r".*/info/refs.*").mock(
            return_value=httpx.Response(200, content=b"001e# service=git-upload-pack\n")
        )
        async with _mode_client("read-only") as c:
            resp = await c.get("/git/group/proj.git/info/refs?service=git-upload-pack")

    assert resp.status_code == 200
    assert route.call_count == 1
    # read token is used (not write)
    auth = route.calls.last.request.headers["authorization"]
    assert "READ-TOKEN" in base64.b64decode(auth.split(" ", 1)[1]).decode()
