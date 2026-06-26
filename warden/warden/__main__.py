"""uvicorn bootstrap with reconcile-before-open and periodic reconcile (W8.2)."""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from .app import create_admin_app, create_app
from .audit import AuditLog
from .config import ConfigError, from_env
from .context import AppContext
from .state import State
from .upstream import Upstream


async def _periodic_reconcile(ctx: AppContext) -> None:
    while True:
        await asyncio.sleep(ctx.cfg.reconcile_interval_s)
        try:
            ctx.state.prune()
            await ctx.reconcile()
        except Exception as exc:  # never crash the loop
            print(f"warden: periodic reconcile error: {exc}", file=sys.stderr)


async def _serve() -> None:
    cfg = from_env()
    upstream = Upstream(cfg)
    state = State(cfg.state_db_path)
    audit = AuditLog(cfg.audit_log_path)
    audit.start()
    ctx = AppContext(cfg, upstream, state, audit)

    # Reconcile BEFORE opening the agent port (§6.11): GitLab truth dominates.
    await ctx.resolve_service_account()
    if not await ctx.reconcile():
        print("warden: initial reconcile incomplete — state stays locked", file=sys.stderr)

    agent = uvicorn.Server(
        uvicorn.Config(create_app(ctx), host="0.0.0.0", port=cfg.agent_port, log_level="info")
    )
    admin = uvicorn.Server(
        uvicorn.Config(create_admin_app(ctx), host="0.0.0.0", port=cfg.admin_port, log_level="warning")
    )
    reconcile_task = asyncio.create_task(_periodic_reconcile(ctx))
    try:
        await asyncio.gather(agent.serve(), admin.serve())
    finally:
        reconcile_task.cancel()
        await audit.stop()
        await upstream.aclose()
        state.close()


def main() -> None:
    try:
        asyncio.run(_serve())
    except ConfigError as exc:
        print(f"warden: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
