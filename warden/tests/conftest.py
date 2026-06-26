from __future__ import annotations

from typing import AsyncIterator

import httpx
import pytest
import respx

from warden.app import create_app
from warden.audit import AuditLog
from warden.config import Config
from warden.context import AppContext
from warden.state import State
from warden.upstream import Upstream

UPSTREAM = "https://gitlab.example"


@pytest.fixture
def cfg() -> Config:
    return Config(
        branch_prefix="claude/",
        max_open_mrs=5,
        max_open_branches=10,
        max_writes_per_hour=60,
        allowed_projects=("group/proj",),
        api_url=f"{UPSTREAM}/api/v4",
        git_base=UPSTREAM,
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
def ctx(cfg, state) -> AppContext:
    upstream = Upstream(cfg)
    audit = AuditLog("-")
    ctx = AppContext(cfg, upstream, state, audit)
    ctx.service_account_id = 42
    return ctx


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
