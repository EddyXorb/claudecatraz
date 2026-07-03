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
from warden.guards.gitlab.forge import GitForge
from warden.guards.gitlab.upstream import Upstream

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
def forge(cfg, state) -> GitForge:
    upstream = Upstream(cfg)
    audit = AuditLog("-")
    return GitForge(cfg, upstream, state, audit)


@pytest.fixture
def ctx(cfg, state) -> AppContext:
    upstream = Upstream(cfg)
    audit = AuditLog("-")
    return build_context(cfg, upstream, state, audit)


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
    await ctx.forge.upstream.aclose()
