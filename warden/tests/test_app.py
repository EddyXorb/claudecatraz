"""app.py: health endpoint and the admin-only read-only audit tail."""

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
    # Per-guard reconcile status; the fixture unlocked both guards.
    assert body["reconciled"] == {"api": True, "git": True}


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
    await ctx.router.aclose()


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
    await ctx.router.aclose()


async def test_policy_route_reports_the_effective_table(cfg):
    # catraz doctor reads this per-host route.
    ctx = build_context(cfg, State(":memory:"), AuditLog("-"))
    async with await _admin_client(ctx) as c:
        resp = await c.get("/policy")
    assert resp.status_code == 200
    body = resp.json()
    host_report = body["hosts"]["gitlab.example"]
    ids = {row["id"] for row in host_report["catalog"]}
    assert "mr.create" in ids and "branch.create" in ids and "mr.merge" in ids
    # git transport rows appear with full rows, not just names.
    assert "git.read" in ids and "git.receive_pack" in ids
    assert "project.mr.create" in host_report["actions"]
    # repo.branch.create is default-on in the new vocabulary.
    assert "repo.branch.create" in host_report["actions"]
    # merge is a named denial, not a hardcoded builtin_deny string.
    assert "project.mr.merge" in host_report["denials"]
    await ctx.router.aclose()


async def test_policy_route_reflects_activation_config(cfg):
    # A per-endpoint `actions` override changes only that host's section.
    endpoint = cfg.git_endpoints[0]
    activated = replace(
        cfg,
        git_endpoints=(replace(endpoint, actions=("project.mr.create", "project.issue.create")),),
    )
    ctx = build_context(activated, State(":memory:"), AuditLog("-"))
    async with await _admin_client(ctx) as c:
        resp = await c.get("/policy")
    body = resp.json()
    host_report = body["hosts"]["gitlab.example"]
    assert set(host_report["actions"]) == {"project.mr.create", "project.issue.create"}
    await ctx.router.aclose()


async def test_viewer_serves_the_static_html_page(cfg):
    # _VIEWER_HTML loads from a package asset, not an inline string.
    ctx = build_context(cfg, State(":memory:"), AuditLog("-"))
    async with await _admin_client(ctx) as c:
        resp = await c.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Warden Audit Log" in resp.text
    assert "<script>" in resp.text
    await ctx.router.aclose()
