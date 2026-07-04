"""Forge-neutral httpx transport: token injection and project mapping.

Read- vs. write-token chosen per :class:`~warden.core.model.Decision`. REST
uses ``PRIVATE-TOKEN`` header; git Smart-HTTP uses HTTP-Basic ``oauth2:<token>``.
Shared by both the git guard and the GitLab REST-API guard — a core module,
not a guard-owned one, so neither guard depends on the other to reach
upstream (§07 Punkt 6). The git guard depends on this module only, never on
anything under ``guards.gitlab_api``.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Optional
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
}
# content-encoding is dropped because the warden hands the client a *decoded* body
# (httpx decompresses via .content / aiter_bytes). Forwarding a stale "gzip" header
# alongside already-decompressed bytes makes the client try to gunzip plain data →
# "compressed data" garbage. Strip it so body and headers stay consistent.
_DROP_RESPONSE_HEADERS = {"content-encoding", "transfer-encoding", "connection", "content-length"}


def project_id(project: str) -> str:
    """URL-encode ``group/sub/proj`` → ``group%2Fsub%2Fproj`` for the REST path."""
    return quote(normalize_project(project), safe="")


def base_urls(endpoint: GitEndpoint) -> tuple[str, Optional[str]]:
    """Base URLs derived from ``endpoint.host`` + ``endpoint.type`` (step 03,
    point 1) — the replacement for the old free-form ``Config.api_url``: every
    host is explicit, and its URL form follows straight from its declared
    ``type``, never from an env var.

    Returns ``(git_base, api_base)``; ``api_base`` is ``None`` for a type with
    no REST surface (``plain``). ``github`` is reserved (rejected at parse
    time, step 01) until its guard exists, so it never reaches here.
    """
    if endpoint.type == "gitlab":
        return f"https://{endpoint.host}", f"https://{endpoint.host}/api/v4"
    if endpoint.type == "plain":
        return f"https://{endpoint.host}", None
    raise ValueError(f"base_urls: unsupported endpoint type {endpoint.type!r}")


class Upstream:
    """One endpoint's transport: base URLs + read/write tokens, forge-neutral.

    Built exclusively by :class:`UpstreamRouter` from a :class:`GitEndpoint`
    (never from a whole :class:`Config` clone) — the endpoint's own
    ``host``/``type`` and its resolved :class:`~.config.HostCredentials` are
    the only inputs a request needs once the router has resolved it.
    """

    def __init__(
        self,
        *,
        git_base: str,
        api_base: Optional[str],
        read_token: str,
        write_token: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._git_base = git_base
        self._api_base = api_base
        self._read_token = read_token
        self._write_token = write_token
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- token headers ---------------------------------------------------------
    def _rest_token(self, token: TokenKind) -> str:
        return self._read_token if token == TokenKind.READ else self._write_token

    def _git_auth_header(self, token: TokenKind) -> str:
        secret = self._rest_token(token)
        raw = f"oauth2:{secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    @staticmethod
    def _clean_headers(headers: dict[str, str], drop: set[str]) -> dict[str, str]:
        return {k: v for k, v in headers.items() if k.lower() not in drop}

    # --- REST ------------------------------------------------------------------
    def rest_url(self, path: str) -> str:
        assert self._api_base is not None, "endpoint has no REST base (type has no API surface)"
        return f"{self._api_base}/{path.lstrip('/')}"

    async def get_json(self, path: str, token: TokenKind) -> httpx.Response:
        resp = await self._client.get(
            self.rest_url(path),
            headers={"PRIVATE-TOKEN": self._rest_token(token)},
        )
        return resp

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
        req_headers["PRIVATE-TOKEN"] = self._rest_token(token)
        req = self._client.build_request(
            method, self.rest_url(path), headers=req_headers, content=content
        )
        return await self._client.send(req, stream=True)

    # --- git Smart-HTTP --------------------------------------------------------
    def git_url(self, project: str, suffix: str) -> str:
        project = normalize_project(project)
        return f"{self._git_base}/{project}.git/{suffix.lstrip('/')}"

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
    """Host → Upstream resolution (§2), shared by the git guard and the
    REST-API guard so neither re-derives its own routing. One shared
    ``httpx.AsyncClient`` (connection pooling) regardless of how many hosts
    are configured.

    Built straight from ``cfg.git_endpoints`` (step 03): one ``Upstream`` per
    endpoint whose :meth:`Config.access_mode` is not ``"closed"`` — an
    endpoint with no usable read credential never gets a routable ``Upstream``
    at all. Every host is explicit; there is no single-target special case.
    :meth:`resolve` normalises the raw ``Host`` header
    (:meth:`Config.normalize_host`) and looks it up in that map, returning
    ``None`` for an unknown *or* closed host (default-deny, R6).
    """

    def __init__(self, cfg: Config, *, client: Optional[httpx.AsyncClient] = None) -> None:
        self._cfg = cfg
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0))
        self._by_host: dict[str, Upstream] = {}
        for endpoint in cfg.git_endpoints:
            if cfg.access_mode(endpoint.host) == "closed":
                continue
            git_base, api_base = base_urls(endpoint)
            normalized = cfg.normalize_host(endpoint.host)
            creds = cfg.git_credentials.get(normalized, HostCredentials())
            self._by_host[normalized] = Upstream(
                git_base=git_base,
                api_base=api_base,
                read_token=creds.read_token,
                write_token=creds.write_token,
                client=self._client,
            )

    def resolve(self, host_header: str) -> Optional[Upstream]:
        """Resolve the raw ``Host`` header to this request's ``Upstream``.

        ``None`` means an unknown or ``closed`` host (default-deny) — the
        caller must turn that into a denial, never fall back to some default
        upstream.
        """
        return self._by_host.get(self._cfg.normalize_host(host_header))

    def for_host(self, host: str) -> Upstream:
        """Direct, non-header lookup for reconcile: ``host`` must be one this
        router actually built an ``Upstream`` for (an open endpoint's host
        from ``cfg.effective_hosts``), so the lookup never misses."""
        return self._by_host[self._cfg.normalize_host(host)]

    async def aclose(self) -> None:
        await self._client.aclose()


def stream_upstream(resp: httpx.Response) -> StreamingResponse:
    """Relay a streamed upstream response to the client, closing it when done.

    Shared by REST and git guards; body is never buffered.
    """

    async def body_iter() -> AsyncIterator[bytes]:
        try:
            # aiter_bytes (not aiter_raw): httpx transparently decompresses the
            # upstream content-encoding, so the client receives a plain body that
            # matches the (content-encoding-stripped) headers — readable by clients
            # that never negotiated gzip (curl, glab, the GitLab MCP, …).
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


async def get_paginated(upstream: Upstream, path: str) -> list[Any]:
    """Fetch every page of a GitLab-shaped list endpoint (W8.2).

    Without this a project with >100 agent branches/MRs would only count the
    first page, undercount the quota, and wrongly ``allow`` further writes.
    Follows the ``X-Next-Page`` header until it is empty. Generic REST-listing
    helper on the transport, not a forge concept — reused by the git guard's
    own branch reconcile and the GitLab REST-API guard's own MR reconcile,
    so neither depends on the other for it.
    """
    items: list[Any] = []
    page = 1
    while page:
        sep = "&" if "?" in path else "?"
        resp = await upstream.get_json(f"{path}{sep}per_page=100&page={page}", TokenKind.READ)
        resp.raise_for_status()
        items.extend(resp.json())
        nxt = resp.headers.get("x-next-page", "")
        page = int(nxt) if nxt else 0
    return items


async def for_each_host_project(
    cfg: Config,
    router: UpstreamRouter,
    label: str,
    fn: Callable[[Upstream, str, str], Awaitable[None]],
) -> bool:
    """Shared fail-safe reconcile loop (§6.11, §07 Punkt 8 follow-up): iterate
    every configured host (``cfg.effective_hosts``) times every allowed
    project, calling ``fn(upstream, host, project)`` for each combination.

    Forge-neutral on purpose — both the git guard's branch reconcile and the
    REST-API guard's MR reconcile had their own copy of this exact double
    loop; this is the one definition, living in ``core`` so neither guard
    depends on the other to get it (§07 Punkt 6). ``fn`` carries all the
    domain-specific work (listing + replacing that guard's own state table);
    a combination whose ``fn`` raises is logged (using ``label`` — e.g.
    ``"git"``/``"api"`` — to tell the guards' log lines apart) and skipped,
    never aborting the rest of the loop. Returns True only if every
    combination completed without raising; False tells the caller to keep its
    state fail-closed-locked rather than trust an undercounted/stale view.
    """
    ok = True
    for host in cfg.effective_hosts:
        upstream = router.for_host(host)
        for project in cfg.allowed_projects:
            try:
                await fn(upstream, host, project)
            except Exception as exc:  # keep state locked on any failure
                log.error("%s reconcile failed for %s@%s: %s", label, project, host, exc)
                ok = False
    return ok
