"""Uvicorn bootstrap with reconcile-before-open and periodic reconcile."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys

import uvicorn

from .app import create_admin_app, create_app
from .context import AppContext, build_context
from .core.audit import AuditLog
from .core.config import ConfigError
from .core.config_load import from_env
from .core.logging_setup import configure_logging
from .core.state import SchemaError, State
from .guards.gitlab_api.catalog import CatalogConfigError

log = logging.getLogger("warden")


async def _periodic_reconcile(ctx: AppContext) -> None:
    while True:
        await asyncio.sleep(ctx.cfg.reconcile_interval_s)
        try:
            ctx.state.prune()
            for g in ctx.guards:
                await g.reconcile()
        except Exception as exc:  # never crash the loop
            log.error("periodic reconcile error: %s", exc)


async def _run_servers(ctx: AppContext) -> None:
    """Own the uvicorn lifecycle: agent + admin servers, periodic reconcile,
    and teardown once either server stops."""
    agent = uvicorn.Server(
        uvicorn.Config(create_app(ctx), host="0.0.0.0", port=ctx.cfg.agent_port, log_level="info")
    )
    admin_uds = os.environ.get("ADMIN_UDS")
    if admin_uds:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(admin_uds)  # stale socket von Crash entfernen
        admin_config = uvicorn.Config(create_admin_app(ctx), uds=admin_uds, log_level="warning")
    else:
        admin_config = uvicorn.Config(
            create_admin_app(ctx),
            host=ctx.cfg.admin_host,
            port=ctx.cfg.admin_port,
            log_level="warning",
        )
    admin = uvicorn.Server(admin_config)
    reconcile_task = asyncio.create_task(_periodic_reconcile(ctx))
    try:
        await asyncio.gather(agent.serve(), admin.serve())
    finally:
        reconcile_task.cancel()
        await ctx.aclose()


async def _serve() -> None:
    cfg = from_env()
    configure_logging(cfg.log_path)

    if cfg.gitlab_enabled and not cfg.allowed_projects:
        log.warning(
            "allowed_projects is empty — ALL GitLab operations will be denied "
            "(R-rules) until a project is added to warden.toml. The dev-env "
            "still starts for offline work."
        )
    state = State(cfg.state_db_path)
    audit = AuditLog(cfg.audit_log_path)
    audit.start()
    ctx = build_context(cfg, state, audit)

    # Reconcile BEFORE opening the agent port: GitLab truth dominates. This is a
    # global lifecycle guarantee (port-open timing), so it lives in the runtime
    # and stays out of any individual guard (won't-do: see §07 Punkt 5).
    for g in ctx.guards:
        await g.startup()
    ok = True
    for g in ctx.guards:
        ok = (await g.reconcile()) and ok
    if not ok:
        log.error("initial reconcile incomplete — state stays locked")

    await _run_servers(ctx)


def main() -> None:
    try:
        asyncio.run(_serve())
    except (ConfigError, CatalogConfigError, SchemaError) as exc:
        # All three are fail-closed startup aborts: bad config (shape or
        # catalog-activation), or a state DB this build cannot understand.
        # None should ever surface as a traceback — a clean message and a
        # non-zero exit is the contract.
        print(f"warden: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
