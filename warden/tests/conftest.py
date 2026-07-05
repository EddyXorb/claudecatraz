from __future__ import annotations

from typing import AsyncIterator

import httpx
import pytest
import respx

from warden.app import create_app
from warden.context import AppContext, build_context
from warden.core.audit import AuditLog
from warden.core.config import Config, GitEndpoint, HostCredentials
from warden.core.state import State
from warden.core.transport import UpstreamRouter
from warden.guards.git.gitlab.guard import ApiGuard

UPSTREAM = "https://gitlab.example"
HOST = "gitlab.example"


@pytest.fixture
def cfg() -> Config:
    return Config(
        branch_prefixes=("claude/",),
        max_open_mrs=5,
        max_open_branches=10,
        max_writes_per_hour=60,
        allowed_projects=("group/proj",),
        state_db_path=":memory:",
        git_endpoints=(GitEndpoint(host=HOST, type="gitlab"),),
        git_credentials={HOST: HostCredentials(read_token="READ-TOKEN", write_token="WRITE-TOKEN")},
    )


@pytest.fixture
def state() -> State:
    st = State(":memory:")
    # Unlock the quota view for tests — locks are per guard now, so unlock both.
    st.mark_reconciled("git")
    st.mark_reconciled("api")
    return st


@pytest.fixture
def api_guard(cfg, state) -> ApiGuard:
    return ApiGuard(cfg, state, AuditLog("-"), UpstreamRouter(cfg))


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
    # base_url's host becomes the default `Host` header on every request — it
    # must be the `cfg` fixture's own configured endpoint host (step 03: host
    # routing is real default-deny now, so an arbitrary/no-op host is denied).
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{HOST}") as c:
        yield c
    await ctx.router.aclose()
