from __future__ import annotations

from typing import AsyncIterator

import httpx
import pytest
import respx

from warden.app import create_app
from warden.context import AppContext, build_context
from warden.core.audit import AuditLog
from warden.core.config import Config
from warden.core.state import State
from warden.core.transport import Upstream
from warden.guards.gitlab_api.guard import ApiGuard

UPSTREAM = "https://gitlab.example"


@pytest.fixture
def cfg() -> Config:
    return Config(
        branch_prefixes=("claude/",),
        max_open_mrs=5,
        max_open_branches=10,
        max_writes_per_hour=60,
        allowed_projects=("group/proj",),
        api_url=f"{UPSTREAM}/api/v4",
        read_token="READ-TOKEN",
        write_token="WRITE-TOKEN",
        state_db_path=":memory:",
    )


@pytest.fixture
def state() -> State:
    st = State(":memory:")
    st.mark_reconciled()  # unlock the quota view for tests
    return st


@pytest.fixture
def api_guard(cfg, state) -> ApiGuard:
    return ApiGuard(cfg, state, AuditLog("-"), Upstream(cfg))


@pytest.fixture
def ctx(cfg, state) -> AppContext:
    audit = AuditLog("-")
    return build_context(cfg, state, audit)


@pytest.fixture
def respx_router():
    with respx.mock(base_url=UPSTREAM, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client(ctx) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(ctx)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://warden") as c:
        yield c
    await ctx.upstream.aclose()
