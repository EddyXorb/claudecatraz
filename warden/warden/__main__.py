"""uvicorn bootstrap with reconcile-before-open and periodic reconcile (W8.2)."""

from __future__ import annotations

import asyncio
import contextlib
import os
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
    if cfg.gitlab_enabled and not cfg.allowed_projects:
        print(
            "warden: WARNING: allowed_projects is empty — ALL GitLab operations "
            "will be denied (R-rules) until a project is added to warden.toml. "
            "The dev-env still starts for offline work.",
            file=sys.stderr,
        )
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
    admin_uds = os.environ.get("ADMIN_UDS")
    if admin_uds:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(admin_uds)                      # stale socket von Crash entfernen
        admin_config = uvicorn.Config(create_admin_app(ctx), uds=admin_uds, log_level="warning")
    else:
        admin_config = uvicorn.Config(
            create_admin_app(ctx), host=cfg.admin_host, port=cfg.admin_port, log_level="warning"
        )
    admin = uvicorn.Server(admin_config)
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
