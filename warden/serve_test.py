#!/usr/bin/env python3
"""Dev runner: starts the Warden with --reload, no real GitLab needed.

    uv run dev.py

State stays locked (reconcile can't reach GitLab), so write-quota checks
use the locked=True path — reads and policy-logic still work normally.
Override any env var on the command line:

    GITLAB_URL=https://mygitlab.example.com uv run dev.py
"""
import os

import uvicorn

os.environ.setdefault("GITLAB_URL", "https://gitlab.com")
os.environ.setdefault("GITLAB_READ_TOKEN", "dev-read-token")
os.environ.setdefault("GITLAB_WRITE_TOKEN", "dev-write-token")
os.environ.setdefault("ALLOWED_PROJECTS", "group/proj")
os.environ.setdefault("STATE_DB_PATH", ":memory:")
os.environ.setdefault("AUDIT_LOG_PATH", "-")

from warden.app import create_app
from warden.audit import AuditLog
from warden.config import from_env
from warden.context import AppContext
from warden.state import State
from warden.upstream import Upstream


def factory() -> "uvicorn.typing.ASGIApp":
    cfg = from_env()
    state = State(cfg.state_db_path)
    state.mark_reconciled()  # skip GitLab reconcile in dev
    ctx = AppContext(cfg, Upstream(cfg), state, AuditLog(cfg.audit_log_path))
    ctx.service_account_id = 0
    return create_app(ctx)


if __name__ == "__main__":
    uvicorn.run("serve_test:factory", factory=True, reload=True, port=8080, log_level="debug")
