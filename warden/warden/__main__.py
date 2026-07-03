"""Uvicorn bootstrap with reconcile-before-open and periodic reconcile."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys

import uvicorn

from .app import create_admin_app, create_app
from .context import AppContext, build_context
from .core.audit import AuditLog
from .core.config import ConfigError
from .core.config_load import from_env
from .core.state import SchemaError, State
from .guards.gitlab.upstream import Upstream
from .guards.gitlab_api.catalog import (
    CatalogConfigError,
    StartgateFailure,
    build_effective_table,
    run_startgate,
)


async def _periodic_reconcile(ctx: AppContext) -> None:
    while True:
        await asyncio.sleep(ctx.cfg.reconcile_interval_s)
        try:
            ctx.state.prune()
            for g in ctx.guards:
                await g.reconcile()
        except Exception as exc:  # never crash the loop
            print(f"warden: periodic reconcile error: {exc}", file=sys.stderr)


async def _serve() -> None:
    cfg = from_env()

    # Build the effective endpoint table (raises ConfigError on any fail-closed
    # activation-config problem) and run its startgate — every activated entry's
    # deny-probes, plus built-in invariants' global probes — before anything else.
    # Pure, offline, no state DB / upstream: earliest possible fail-closed abort point.
    table = build_effective_table(cfg, cfg.endpoint_enable)
    run_startgate(cfg, table)

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
    ctx = build_context(cfg, upstream, state, audit)

    # Reconcile BEFORE opening the agent port: GitLab truth dominates.
    for g in ctx.guards:
        await g.startup()
    ok = True
    for g in ctx.guards:
        ok = (await g.reconcile()) and ok
    if not ok:
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
    except (ConfigError, CatalogConfigError, SchemaError, StartgateFailure) as exc:
        # All four are fail-closed startup aborts (A9): bad config (shape or
        # catalog-activation), a state DB this build cannot understand, or a
        # catalog deny-probe that would have been allowed. None should ever
        # surface as a traceback — a clean message and a non-zero exit is
        # the contract.
        print(f"warden: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
