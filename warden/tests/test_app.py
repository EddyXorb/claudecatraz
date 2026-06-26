"""app.py (W3, §6.8): health endpoint and the admin-only read-only audit tail."""

from __future__ import annotations

from dataclasses import replace

import httpx

from warden.app import create_admin_app
from warden.audit import AuditLog
from warden.context import AppContext
from warden.state import State
from warden.upstream import Upstream


async def test_healthz_reports_reconcile_and_service_account(client):
    # `client` serves the agent app with the reconciled state fixture + SA 42.
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["reconciled"] is True
    assert body["service_account_id"] == 42


async def _admin_client(ctx):
    transport = httpx.ASGITransport(app=create_admin_app(ctx))
    return httpx.AsyncClient(transport=transport, base_url="http://admin")


async def test_audit_tail_returns_log_contents(cfg, tmp_path):
    logf = tmp_path / "audit.jsonl"
    logf.write_text('{"a":1}\n{"a":2}\n')
    ctx = AppContext(replace(cfg, audit_log_path=str(logf)), Upstream(cfg), State(":memory:"), AuditLog("-"))
    async with await _admin_client(ctx) as c:
        resp = await c.get("/audit")
    assert resp.status_code == 200
    assert resp.text == '{"a":1}\n{"a":2}\n'
    await ctx.upstream.aclose()


async def test_audit_tail_empty_when_log_missing(cfg, tmp_path):
    # Missing log file is not an error for the tail endpoint — returns empty 200.
    ctx = AppContext(replace(cfg, audit_log_path=str(tmp_path / "nope.jsonl")), Upstream(cfg), State(":memory:"), AuditLog("-"))
    async with await _admin_client(ctx) as c:
        resp = await c.get("/audit")
    assert resp.status_code == 200
    assert resp.text == ""
    await ctx.upstream.aclose()
