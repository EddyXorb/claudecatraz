"""app.py (W3, §6.8): health endpoint and the admin-only read-only audit tail."""

from __future__ import annotations

from dataclasses import replace

import httpx

from warden.app import create_admin_app
from warden.context import build_context
from warden.core.audit import AuditLog
from warden.core.state import State


async def test_healthz_reports_reconcile_status(client):
    # `client` serves the agent app with the reconciled state fixture.
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["reconciled"] is True


async def _admin_client(ctx):
    transport = httpx.ASGITransport(app=create_admin_app(ctx))
    return httpx.AsyncClient(transport=transport, base_url="http://admin")


async def test_audit_tail_returns_log_contents(cfg, tmp_path):
    logf = tmp_path / "audit.jsonl"
    logf.write_text('{"a":1}\n{"a":2}\n')
    ctx = build_context(replace(cfg, audit_log_path=str(logf)), State(":memory:"), AuditLog("-"))
    async with await _admin_client(ctx) as c:
        resp = await c.get("/audit")
    assert resp.status_code == 200
    assert resp.text == '{"a":1}\n{"a":2}\n'
    await ctx.upstream.aclose()


async def test_audit_tail_empty_when_log_missing(cfg, tmp_path):
    # Missing log file is not an error for the tail endpoint — returns empty 200.
    ctx = build_context(
        replace(cfg, audit_log_path=str(tmp_path / "nope.jsonl")),
        State(":memory:"),
        AuditLog("-"),
    )
    async with await _admin_client(ctx) as c:
        resp = await c.get("/audit")
    assert resp.status_code == 200
    assert resp.text == ""
    await ctx.upstream.aclose()


async def test_policy_route_reports_the_effective_table(cfg):
    # §04.3: catraz doctor / allow-endpoint read this route.
    ctx = build_context(cfg, State(":memory:"), AuditLog("-"))
    async with await _admin_client(ctx) as c:
        resp = await c.get("/policy")
    assert resp.status_code == 200
    body = resp.json()
    ids = {row["id"] for row in body["catalog"]}
    assert "mr.create" in ids and "branch.create" in ids
    mr_create = next(row for row in body["catalog"] if row["id"] == "mr.create")
    assert mr_create["default"] is True and mr_create["active"] is True
    branch_create = next(row for row in body["catalog"] if row["id"] == "branch.create")
    assert branch_create["default"] is False and branch_create["active"] is False
    assert body["builtin_deny"] == ["mr.merge"]
    await ctx.upstream.aclose()


async def test_policy_route_reflects_activation_config(cfg, tmp_path):
    from dataclasses import replace

    activated = replace(cfg, endpoint_enable=("mr.create", "branch.create"))
    ctx = build_context(activated, State(":memory:"), AuditLog("-"))
    async with await _admin_client(ctx) as c:
        resp = await c.get("/policy")
    body = resp.json()
    branch_create = next(row for row in body["catalog"] if row["id"] == "branch.create")
    assert branch_create["active"] is True
    assert branch_create["enabled_via"] == "config:branch.create"
    mr_note = next(row for row in body["catalog"] if row["id"] == "mr.note")
    assert mr_note["active"] is False  # not in this test's enable list
    await ctx.upstream.aclose()


async def test_viewer_serves_the_static_html_page(cfg):
    # F7: _VIEWER_HTML now loads from warden/static/viewer.html (a package asset,
    # not an inline string in routing code) — the endpoint must still serve it.
    ctx = build_context(cfg, State(":memory:"), AuditLog("-"))
    async with await _admin_client(ctx) as c:
        resp = await c.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Warden Audit Log" in resp.text
    assert "<script>" in resp.text
    await ctx.upstream.aclose()
