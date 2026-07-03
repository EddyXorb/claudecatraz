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
from .guards.gitlab.upstream import Upstream
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


# TODO: tidy up this function and split it into smaller functions.
async def _serve() -> None:
    cfg = from_env()
    configure_logging(cfg.log_path)

    if cfg.gitlab_enabled and not cfg.allowed_projects:
        log.warning(
            "allowed_projects is empty — ALL GitLab operations will be denied "
            "(R-rules) until a project is added to warden.toml. The dev-env "
            "still starts for offline work."
        )
    # TODO: this leaks from gitlab_api, should not be here. If is is really needed it
    # can persist in the Guards that the build-context creates, but as part of the
    # their initialization (make it a member of the guard class) without the context
    # builder needing to know about it.
    upstream = Upstream(cfg)
    state = State(cfg.state_db_path)
    audit = AuditLog(cfg.audit_log_path)
    audit.start()
    ctx = build_context(cfg, upstream, state, audit)

    # Reconcile BEFORE opening the agent port: GitLab truth dominates.
    # TODO: consider moving this into the Gitlab-Guard directly.
    # The fact the the context holds all guards alive (and they are no one-shot
    # objects anymore) makes this possible
    for g in ctx.guards:
        await g.startup()
    ok = True
    for g in ctx.guards:
        ok = (await g.reconcile()) and ok
    if not ok:
        log.error("initial reconcile incomplete — state stays locked")

    agent = uvicorn.Server(
        uvicorn.Config(create_app(ctx), host="0.0.0.0", port=cfg.agent_port, log_level="info")
    )
    admin_uds = os.environ.get("ADMIN_UDS")
    if admin_uds:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(admin_uds)  # stale socket von Crash entfernen
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
    except (ConfigError, CatalogConfigError, SchemaError) as exc:
        # All three are fail-closed startup aborts: bad config (shape or
        # catalog-activation), or a state DB this build cannot understand.
        # None should ever surface as a traceback — a clean message and a
        # non-zero exit is the contract.
        print(f"warden: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
