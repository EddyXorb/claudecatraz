"""Reads, GraphQL, type scoping, upstream layouts and raw-path bypass attempts."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from warden.core.config import Config, GitEndpoint, HostCredentials
from warden.core.model import TokenKind
from warden.core.transport import UpstreamLayout, UpstreamRouter
from warden.guards.git.endpoints import composes, upstream_layout

from .github_helpers import (
    ALL_GITHUB_ACTIONS,
    API,
    READ,
    REPO,
    UP,
    WRITE,
    endpoint,
    harness,
    make_cfg,
    raw_asgi,
)


# --- reads ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "path,action",
    [
        (f"{REPO}/contents/a/b.txt?ref=claude/x", "repo.read"),
        (f"{REPO}/git/blobs/abc", "repo.read"),
        (f"{REPO}/commits/abc/status", "repo.read"),
        (f"{REPO}/readme/docs", "repo.read"),
        (REPO, "project.read"),
        (f"{REPO}/pulls/7/files", "project.read"),
        (f"{REPO}/actions/runs/9/logs", "project.read"),
        (f"{REPO}/issues/7/comments", "project.read"),
        ("/api/v3/search/repositories?q=x", "instance.projects.read"),
        ("/api/v3/search/users?q=x", "instance.users.read"),
        ("/api/v3/orgs", "instance.projects.read"),
        ("/api/v3/user/repos", "instance.users.read"),
    ],
)
async def test_reads_forwarded_with_read_token(path, action):
    async with harness() as h:
        fwd = h.forward_route()
        resp = await h.client.get(path)
        assert resp.status_code == 200
        req = fwd.calls.last.request
        assert req.headers["authorization"] == f"Bearer {READ}"
        assert str(req.url) == API + path.removeprefix("/api/v3")
        assert h.last["actions"] == (action,) and h.last["kind"] is None
        assert "SECRET" not in resp.text + str(resp.headers)


async def test_head_read_forwarded_as_head():
    async with harness() as h:
        fwd = h.forward_route()
        resp = await h.client.head(f"{REPO}/contents/a")
        assert resp.status_code == 200 and fwd.calls.last.request.method == "HEAD"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v3/repos/evil/repo/contents/a",
        "/api/v3/repos/evil/repo",
        "/api/v3/repos/OCTO/REPO/contents/a",
        "/api/v3/repos/octo/repo%2F..%2Fevil/contents",
        "/api/v3/repos/octo/other/pulls",
    ],
)
async def test_reads_of_other_repositories_denied(path):
    async with harness() as h:
        resp = await h.client.get(path)
        assert resp.status_code == 403 and "not in allowlist" in resp.json()["reason"]
        assert not h.mock.calls


@pytest.mark.parametrize(
    "path",
    [
        "/api/v3/repositories/123",
        "/api/v3/repositories/123/contents/a",
        "/api/v3/search/code?q=password",
        "/api/v3/search/commits?q=secret",
        "/api/v3/search/labels?q=x",
        "/api/v3/gists",
        "/api/v3/notifications",
        "/api/v3/app",
        "/api/v3/enterprises/x",
        "/api/v3/",
        "/api/v3/usersx",
        "/api/v3/repos/octo",
    ],
)
async def test_unmodelled_reads_denied(path):
    async with harness() as h:
        resp = await h.client.get(path)
        assert resp.status_code == 403
        assert "read endpoint not in allowlist" in resp.json()["reason"]
        assert not h.mock.calls


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", ["/api/v3/graphql", "/api/v3/graphql/", "/api/graphql"])
async def test_graphql_denied(method, path):
    async with harness() as h:
        resp = await h.client.request(method, path, json={"query": "mutation{}"})
        assert resp.status_code == 403
        assert not h.mock.calls
        if path.startswith("/api/v3"):
            assert "GraphQL" in resp.json()["reason"]


async def test_options_never_forwarded():
    async with harness() as h:
        resp = await h.client.options(f"{REPO}/pulls")
        assert resp.status_code == 405 and not h.mock.calls


async def test_repo_read_disabled_denies_contents_but_not_project_reads():
    actions = tuple(a for a in ALL_GITHUB_ACTIONS if a != "repo.read")
    async with harness(make_cfg(actions=actions)) as h:
        h.forward_route()
        resp = await h.client.get(f"{REPO}/contents/a")
        assert resp.status_code == 403 and "repo.read not enabled" in resp.json()["reason"]
        assert (await h.client.get(f"{REPO}/pulls")).status_code == 200
        assert len(h.mock.calls) == 1


async def test_encoded_content_path_cannot_bypass_disabled_repo_read():
    actions = tuple(a for a in ALL_GITHUB_ACTIONS if a != "repo.read")
    async with harness(make_cfg(actions=actions)) as h:
        h.forward_route()
        resp = await h.client.get(f"{REPO}/%63ontents/a")
        assert resp.status_code == 403
        assert not h.mock.calls


async def test_instance_reads_disabled_per_endpoint():
    async with harness(make_cfg(actions=("repo.read", "project.read"))) as h:
        for path in ("/api/v3/user", "/api/v3/meta", "/api/v3/search/issues"):
            resp = await h.client.get(path)
            assert resp.status_code == 403 and "not enabled" in resp.json()["reason"]
        assert not h.mock.calls


# --- dot segments via raw ASGI (httpx would normalise them away) ----------------------------
@pytest.mark.parametrize(
    "method,raw_path",
    [
        ("GET", "/api/v3/repos/octo/repo/../../evil/secret/contents/a"),
        ("GET", "/api/v3/repos/octo/repo/contents/%2e%2e/%2e%2e/%2e%2e/evil/secret"),
        ("GET", "/api/v3/repos/octo/repo/./contents/a"),
        ("PUT", "/api/v3/repos/octo/repo/pulls/7/x/../merge"),
        ("POST", "/api/v3/repos/octo/repo/pulls/../../../../user/repos"),
        ("POST", "/api/v3/repos/octo/repo/actions/workflows/x%2F..%2F..%2Fpulls/dispatches"),
        ("DELETE", "/api/v3/repos/octo/repo/git/refs/heads/claude/../../tags/v1"),
    ],
)
async def test_dot_segments_denied_without_upstream_call(method, raw_path):
    async with harness() as h:
        h.mock.get(f"{UP}/pulls/7").respond(200, json={})
        app = h.client._transport.app
        status, body = await raw_asgi(app, method, raw_path, body=b'{"ref": "claude/x"}')
        assert status == 403
        assert not h.mock.calls


async def test_raw_path_outside_the_literal_prefix_is_not_recognized():
    async with harness() as h:
        app = h.client._transport.app
        status, _ = await raw_asgi(app, "GET", "/api/%76%33/repos/octo/repo/contents/a")
        assert status == 403 and not h.mock.calls


async def test_percent_encoded_owner_resolves_to_the_allowed_project():
    async with harness() as h:
        fwd = h.forward_route()
        resp = await h.client.get("/api/v3/repos/%6Fcto/repo/pulls")
        assert resp.status_code == 200
        assert fwd.calls.last.request.url.raw_path == b"/repos/octo/repo/pulls"


# --- type scoping ------------------------------------------------------------------
def _mixed_cfg() -> Config:
    return Config(
        state_db_path=":memory:",
        git_endpoints=(
            GitEndpoint(host="gl.example", type="gitlab", allowed_projects=("g/p",)),
            endpoint(),
            GitEndpoint(host="plain.example", type="plain", allowed_projects=("repo",)),
        ),
        git_credentials={
            "gl.example": HostCredentials(READ, WRITE),
            "github.com": HostCredentials(READ, WRITE),
            "plain.example": HostCredentials(READ, WRITE),
        },
    )


@pytest.mark.parametrize(
    "host,path,guard",
    [
        ("gl.example", "/api/v3/repos/g/p/contents/a", "github"),
        ("gl.example", "/api/v3/meta", "github"),
        ("plain.example", "/api/v3/meta", "github"),
        ("github.com", "/api/v4/projects/octo%2Frepo/repository/tree", "api"),
        ("github.com", "/api/v4/user", "api"),
    ],
)
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_foreign_type_rest_denied_without_upstream_call(host, path, guard, method):
    async with harness(_mixed_cfg(), host=host) as h:
        resp = await h.client.request(method, path, json={})
        assert resp.status_code == 403
        assert f"not served by the {guard} guard" in resp.json()["reason"]
        assert not h.mock.calls


def test_composes_github():
    cfg = _mixed_cfg()
    assert composes(cfg, "GitHub.com:8080", "github")
    assert composes(cfg, "github.com", "transport")
    assert not composes(cfg, "github.com", "gitlab")
    assert not composes(cfg, "gl.example", "github")


# --- upstream layouts ---------------------------------------------------------------
@pytest.mark.parametrize(
    "host,api_base",
    [
        ("github.com", "https://api.github.com"),
        ("GitHub.com", "https://api.github.com"),
        ("octo.ghe.com", "https://api.octo.ghe.com"),
        ("ghe.example", "https://ghe.example/api/v3"),
        ("ghe.com", "https://ghe.com/api/v3"),
        ("notgithub.com", "https://notgithub.com/api/v3"),
        ("github.com.evil", "https://github.com.evil/api/v3"),
    ],
)
def test_github_layout(host, api_base):
    layout = upstream_layout(GitEndpoint(host=host, type="github"))
    assert layout == UpstreamLayout(
        git_base=f"https://{host.lower()}",
        api_base=api_base,
        rest_auth_header="Authorization",
        rest_auth_scheme="Bearer ",
        git_user="x-access-token",
    )


@pytest.mark.parametrize(
    "host,base",
    [("ghe.example", "https://ghe.example/api/v3"), ("octo.ghe.com", "https://api.octo.ghe.com")],
)
async def test_other_github_hosts_forward_to_their_rest_base(host, base):
    cfg = make_cfg(endpoint(host))
    async with harness(cfg, host=host) as h:
        h.mock.get(f"{base}/repos/octo/repo/pulls/7").respond(
            200,
            json={"head": {"ref": "claude/x", "repo": {"id": 1}}, "base": {"repo": {"id": 1}}},
        )
        fwd = h.forward_route(base)
        assert (await h.client.get(f"{REPO}/contents/a")).status_code == 200
        assert str(fwd.calls.last.request.url) == f"{base}/repos/octo/repo/contents/a"
        resp = await h.client.post(f"{REPO}/pulls/7/comments", json={"body": "b"})
        assert resp.status_code == 200
        assert fwd.calls.last.request.headers["authorization"] == f"Bearer {WRITE}"
        assert not any("api.github.com" in str(c.request.url) for c in h.mock.calls)


async def test_git_transport_uses_x_access_token_basic_auth():
    cfg = make_cfg()
    router = UpstreamRouter(cfg, upstream_layout)
    with respx.mock() as mock:
        route = mock.get("https://github.com/octo/repo.git/info/refs").respond(200)
        await router.for_host("github.com").git_get(
            "octo/repo", "info/refs", params={}, token=TokenKind.READ
        )
        sent = route.calls.last.request.headers["authorization"]
    assert base64.b64decode(sent.removeprefix("Basic ")) == f"x-access-token:{READ}".encode()
    await router.aclose()


@pytest.mark.parametrize(
    "url,path",
    [
        (f"{API}/repositories/1/pulls?page=2", "repositories/1/pulls?page=2"),
        ("https://api.github.com.evil/x", None),
        ("https://evil.example/https://api.github.com/x", None),
        ("http://api.github.com/x", None),
        (API, None),
        ("/repos/octo/repo/pulls?page=2", None),
    ],
)
def test_rest_path_of(url, path):
    router = UpstreamRouter(make_cfg(), upstream_layout)
    assert router.for_host("github.com").rest_path_of(url) == path


def test_rest_path_of_without_rest_base():
    cfg = Config(
        git_endpoints=(GitEndpoint(host="plain.example", type="plain"),),
        git_credentials={"plain.example": HostCredentials(READ, WRITE)},
    )
    router = UpstreamRouter(cfg, upstream_layout)
    assert router.for_host("plain.example").rest_path_of("https://plain.example/x") is None


async def test_response_tokens_absent_even_on_upstream_errors():
    async with harness() as h:
        h.mock.get(f"{UP}/pulls").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        resp = await h.client.get(f"{REPO}/pulls")
        assert resp.status_code == 401 and "SECRET" not in resp.text + str(resp.headers)
