"""Guards serve and reconcile only hosts whose endpoint type composes them.

A REST guard must never forward its forge's request shape to a host of
another type, and one plain endpoint must never lock any guard."""

from __future__ import annotations

import httpx
import respx
from git_wire import INFO_REFS, advertisement_response

from warden.app import create_app
from warden.context import build_context
from warden.core.audit import AuditLog
from warden.core.config import Config, GitEndpoint, HostCredentials
from warden.core.guard import served_gate
from warden.core.state import State
from warden.guards.git.endpoints import composes, open_hosts_composing

GL, PLAIN, CLOSED = "gl.example", "plain.example", "closed.example"


def _cfg() -> Config:
    return Config(
        state_db_path=":memory:",
        git_endpoints=(
            GitEndpoint(host=GL, type="gitlab", allowed_projects=("g/p",)),
            GitEndpoint(host=PLAIN, type="plain", allowed_projects=("repo",)),
            GitEndpoint(host=CLOSED, type="gitlab", allowed_projects=("g/p",)),
        ),
        git_credentials={
            GL: HostCredentials(read_token="R", write_token="W"),
            PLAIN: HostCredentials(read_token="R", write_token="W"),
        },
    )


def test_composes_follows_the_endpoint_type():
    cfg = _cfg()
    assert composes(cfg, GL, "gitlab") and composes(cfg, GL, "transport")
    assert composes(cfg, PLAIN, "transport") and not composes(cfg, PLAIN, "gitlab")
    assert composes(cfg, "GL.example:8080", "gitlab")  # normalised like every host lookup
    assert not composes(cfg, "unknown.example", "transport")


def test_open_hosts_composing_skips_closed_and_foreign_types():
    cfg = _cfg()
    assert open_hosts_composing(cfg, "gitlab") == (GL,)
    assert open_hosts_composing(cfg, "transport") == (GL, PLAIN)


def test_served_gate_names_the_guard():
    assert served_gate(GL, "api", lambda h: True) is None
    denied = served_gate(PLAIN, "api", lambda h: False)
    assert denied is not None and not denied.allow
    assert denied.reason == "host 'plain.example' is not served by the api guard"


async def test_rest_request_to_a_plain_host_is_denied_without_upstream_call():
    st = State(":memory:")
    st.mark_reconciled("api")
    ctx = build_context(_cfg(), st, AuditLog("-"))
    transport = httpx.ASGITransport(app=create_app(ctx))
    with respx.mock(assert_all_called=False) as mock:  # any upstream call would raise
        async with httpx.AsyncClient(transport=transport, base_url=f"http://{PLAIN}") as c:
            resp = await c.get("/api/v4/projects/repo/repository/tree")
        assert not mock.calls
    assert resp.status_code == 403
    assert "not served by the api guard" in resp.json()["reason"]
    await ctx.router.aclose()


async def test_a_plain_endpoint_never_locks_any_guard():
    st = State(":memory:")
    ctx = build_context(_cfg(), st, AuditLog("-"))
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=INFO_REFS).mock(return_value=advertisement_response("claude/a"))
        mock.get(f"https://{GL}/api/v4/projects/g%2Fp").mock(
            return_value=httpx.Response(200, json={"id": 1})
        )
        mock.get(url__regex=rf"https://{GL}/api/v4/projects/g%2Fp/merge_requests\?.*").mock(
            return_value=httpx.Response(200, json=[])
        )
        assert await ctx.reconcile_all() is True
        assert not any(
            PLAIN in str(c.request.url) and "/api/" in str(c.request.url) for c in mock.calls
        )
    assert st.is_reconciled("git") and st.is_reconciled("api")
    await ctx.router.aclose()
