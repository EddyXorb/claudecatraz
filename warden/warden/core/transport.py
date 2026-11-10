"""Forge-neutral httpx transport: token injection and project mapping.

Read- vs. write-token chosen per Decision; where each surface lives and how
it authenticates comes from an UpstreamLayout the composition root supplies
per endpoint, so no forge is named here.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Optional, Sequence
from urllib.parse import quote

import httpx
from starlette.responses import StreamingResponse

from .config import Config, GitEndpoint, HostCredentials, normalize_project
from .model import TokenKind

log = logging.getLogger("warden")

# Hop-by-hop headers that must not be forwarded verbatim.
_DROP_REQUEST_HEADERS = {
    "host",
    "authorization",
    "private-token",
    "content-length",
    "connection",
    "accept-encoding",
    # The decision is made on the wire method; an upstream must never see another.
    "x-http-method-override",
    "x-http-method",
    "x-method-override",
}
# content-encoding is dropped: the warden hands the client an already-decoded
# body, so a stale "gzip" header would make the client try to gunzip plain data.
_DROP_RESPONSE_HEADERS = {"content-encoding", "transfer-encoding", "connection", "content-length"}


def project_id(project: str) -> str:
    """URL-encode group/sub/proj → group%2Fsub%2Fproj for the REST path."""
    return quote(normalize_project(project), safe="")


@dataclass(frozen=True)
class UpstreamLayout:
    """Where one endpoint's git and REST surfaces live and how each authenticates.

    api_base is None for a type with no REST surface. The REST token goes
    into rest_auth_header as rest_auth_scheme + token; git uses HTTP-Basic
    git_user:token."""

    git_base: str
    api_base: Optional[str]
    rest_auth_header: str
    rest_auth_scheme: str
    git_user: str


LayoutFor = Callable[[GitEndpoint], UpstreamLayout]


class Upstream:
    """One endpoint's transport: base URLs + read/write tokens, forge-neutral.

    Built exclusively by UpstreamRouter from a GitEndpoint, never from a
    whole Config clone.
    """

    def __init__(
        self,
        *,
        layout: UpstreamLayout,
        read_token: str,
        write_token: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._layout = layout
        self._read_token = read_token
        self._write_token = write_token
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- token headers ---------------------------------------------------------
    def _secret(self, token: TokenKind) -> str:
        return self._read_token if token == TokenKind.READ else self._write_token

    def _rest_auth(self, token: TokenKind) -> dict[str, str]:
        layout = self._layout
        return {layout.rest_auth_header: layout.rest_auth_scheme + self._secret(token)}

    def _git_auth_header(self, token: TokenKind) -> str:
        raw = f"{self._layout.git_user}:{self._secret(token)}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    @staticmethod
    def _clean_headers(headers: dict[str, str], drop: set[str]) -> dict[str, str]:
        return {k: v for k, v in headers.items() if k.lower() not in drop}

    # --- REST ------------------------------------------------------------------
    def rest_url(self, path: str) -> str:
        api_base = self._layout.api_base
        assert api_base is not None, "endpoint has no REST base (type has no API surface)"
        return f"{api_base}/{path.lstrip('/')}"

    async def get_json(self, path: str, token: TokenKind) -> httpx.Response:
        return await self._client.get(self.rest_url(path), headers=self._rest_auth(token))

    async def open_rest(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        content: bytes | None,
        token: TokenKind,
    ) -> httpx.Response:
        """Send a REST request and return a *streamed* response (caller closes)."""
        req_headers = self._clean_headers(headers, _DROP_REQUEST_HEADERS)
        req_headers.update(self._rest_auth(token))
        req = self._client.build_request(
            method, self.rest_url(path), headers=req_headers, content=content
        )
        return await self._client.send(req, stream=True)

    # --- git Smart-HTTP --------------------------------------------------------
    def git_url(self, project: str, suffix: str) -> str:
        project = normalize_project(project)
        return f"{self._layout.git_base}/{project}.git/{suffix.lstrip('/')}"

    async def git_get(
        self, project: str, suffix: str, *, params: dict[str, str], token: TokenKind
    ) -> httpx.Response:
        return await self._client.get(
            self.git_url(project, suffix),
            params=params,
            headers={"Authorization": self._git_auth_header(token)},
        )

    async def git_post_stream(
        self,
        project: str,
        suffix: str,
        *,
        body: AsyncIterator[bytes],
        content_type: str,
        token: TokenKind,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> httpx.Response:
        headers = {
            "Authorization": self._git_auth_header(token),
            "Content-Type": content_type,
        }
        if extra_headers:
            headers.update(extra_headers)
        req = self._client.build_request(
            "POST", self.git_url(project, suffix), headers=headers, content=body
        )
        return await self._client.send(req, stream=True)

    @staticmethod
    def response_headers(resp: httpx.Response) -> dict[str, str]:
        return {k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS}


class UpstreamRouter:
    """Host to Upstream resolution, shared by the git and REST-API guards
    so neither re-derives its own routing. One shared httpx.AsyncClient
    regardless of how many hosts are configured. One Upstream is built
    per endpoint whose access_mode is not "closed"; resolve returns None
    for an unknown or closed host (default-deny).
    """

    def __init__(
        self, cfg: Config, layout_for: LayoutFor, *, client: Optional[httpx.AsyncClient] = None
    ) -> None:
        self._cfg = cfg
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0))
        self._by_host: dict[str, Upstream] = {}
        for endpoint in cfg.git_endpoints:
            if cfg.access_mode(endpoint.host) == "closed":
                continue
            normalized = cfg.normalize_host(endpoint.host)
            creds = cfg.git_credentials.get(normalized, HostCredentials())
            self._by_host[normalized] = Upstream(
                layout=layout_for(endpoint),
                read_token=creds.read_token,
                write_token=creds.write_token,
                client=self._client,
            )

    def resolve(self, host_header: str) -> Optional[Upstream]:
        """Resolve the raw Host header to this request's Upstream.

        None means an unknown or closed host (default-deny) — the caller
        must turn that into a denial, never fall back to a default upstream.
        """
        return self._by_host.get(self._cfg.normalize_host(host_header))

    def for_host(self, host: str) -> Upstream:
        """Direct, non-header lookup for reconcile: host must be one this
        router actually built an Upstream for (an open endpoint's host
        from cfg.effective_hosts), so the lookup never misses."""
        return self._by_host[self._cfg.normalize_host(host)]

    async def aclose(self) -> None:
        await self._client.aclose()


def stream_upstream(resp: httpx.Response) -> StreamingResponse:
    """Relay a streamed upstream response to the client, closing it when done.

    Shared by REST and git guards; body is never buffered.
    """

    async def body_iter() -> AsyncIterator[bytes]:
        try:
            # aiter_bytes (not aiter_raw): httpx decompresses so the client
            # receives a plain body matching the stripped headers.
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=resp.status_code,
        headers=Upstream.response_headers(resp),
        media_type=resp.headers.get("content-type"),
    )


async def for_each_host_project(
    cfg: Config,
    router: UpstreamRouter,
    hosts: Sequence[str],
    label: str,
    fn: Callable[[Upstream, str, str], Awaitable[None]],
) -> bool:
    """Shared fail-safe reconcile loop: iterate hosts times each host's own
    allowed project, calling fn(upstream, host, project) for each combination.

    A combination whose fn raises is logged and skipped, never aborting the
    loop. Returns True only if every combination succeeded; False tells the
    caller to keep its state fail-closed-locked. hosts must already be
    narrowed to open endpoints — a closed host raises KeyError here.
    """
    ok = True
    for host in hosts:
        upstream = router.for_host(host)
        endpoint = cfg.endpoint_for(host)
        projects = endpoint.allowed_projects if endpoint is not None else ()
        for project in projects:
            try:
                await fn(upstream, host, project)
            except Exception as exc:  # keep state locked on any failure
                log.error("%s reconcile failed for %s@%s: %s", label, project, host, exc)
                ok = False
    return ok
