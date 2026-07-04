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
import dataclasses
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Optional
from urllib.parse import quote

import httpx
from starlette.responses import StreamingResponse

from .config import Config, normalize_project
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


class Upstream:
    def __init__(self, cfg: Config, client: Optional[httpx.AsyncClient] = None) -> None:
        self._cfg = cfg
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- token headers ---------------------------------------------------------
    def _rest_token(self, token: TokenKind) -> str:
        return self._cfg.read_token if token == TokenKind.READ else self._cfg.write_token

    def _git_auth_header(self, token: TokenKind) -> str:
        secret = self._rest_token(token)
        raw = f"oauth2:{secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    @staticmethod
    def _clean_headers(headers: dict[str, str], drop: set[str]) -> dict[str, str]:
        return {k: v for k, v in headers.items() if k.lower() not in drop}

    # --- REST ------------------------------------------------------------------
    def rest_url(self, path: str) -> str:
        return f"{self._cfg.api_url}/{path.lstrip('/')}"

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
        return f"{self._cfg.git_base}/{project}.git/{suffix.lstrip('/')}"

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


def _host_config(cfg: Config, host: str) -> Config:
    """Build a host-scoped :class:`Config` clone for :class:`Upstream` construction
    (§07 Punkt 8 follow-up, design spike section 2/3).

    ``Upstream`` only ever reads ``api_url``/``git_base`` (a property derived
    from ``api_url``)/``read_token``/``write_token`` off the ``Config`` it is
    given — cloning via :func:`dataclasses.replace` keeps ``Upstream.__init__``
    and every existing ``Upstream(cfg)`` call site (composition root, tests)
    unchanged; only the values the clone carries differ. Base URL is derived
    purely from the host (design spike §3: "Ein Host in der Allowlist bedeutet
    also implizit auch seine URL-Form") — ``GITLAB_URL`` only matters for the
    legacy single-target path (:class:`UpstreamRouter`'s single-``Upstream``
    branch, built straight from ``cfg`` with no cloning).
    """
    creds = cfg.credentials_for(host)
    return dataclasses.replace(
        cfg,
        api_url=f"https://{host}/api/v4",
        read_token=creds.read_token,
        write_token=creds.write_token,
    )


class UpstreamRouter:
    """Host → Upstream resolution (§07 Punkt 8 design spike, section 2),
    shared by the git guard and the REST-API guard so neither re-derives its
    own routing. One shared ``httpx.AsyncClient`` (connection pooling)
    regardless of how many hosts are configured.

    **Single-target default** (``cfg.host_order`` empty — no ``[git.urls]
    hosts`` configured): :meth:`resolve` ignores the ``Host`` header entirely
    and always returns the one ``Upstream`` built straight from ``cfg`` —
    byte-for-byte the pre-multi-target behaviour, so an existing single-host
    deployment sees no change no matter what a client's ``Host`` header says.

    **Multi-target** (``cfg.host_order`` non-empty): :meth:`resolve` uses
    ``Config.resolve_target_host`` (case/port/trailing-dot-insensitive,
    default-deny) to map the request's ``Host`` header to one of the
    pre-built per-host ``Upstream`` instances, or ``None`` on an unknown host.
    """

    def __init__(self, cfg: Config, *, client: Optional[httpx.AsyncClient] = None) -> None:
        self._cfg = cfg
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0))
        if not cfg.host_order:
            self._single: Optional[Upstream] = Upstream(cfg, client=self._client)
            self._by_host: dict[str, Upstream] = {}
        else:
            self._single = None
            self._by_host = {
                host: Upstream(_host_config(cfg, host), client=self._client)
                for host in cfg.host_order
            }

    def resolve(self, host_header: str) -> Optional[Upstream]:
        """Resolve the raw ``Host`` header to this request's ``Upstream``.

        ``None`` means an unknown/unlisted host (default-deny) — the caller
        must turn that into a denial, never fall back to some default
        upstream. Never ``None`` in single-target mode (see class docstring).
        """
        target = self._cfg.resolve_target_host(host_header)
        if target is None:
            return None
        if self._single is not None:
            return self._single
        return self._by_host.get(target)

    def for_host(self, host: str) -> Upstream:
        """Direct, non-header lookup for reconcile (§07 Punkt 8 follow-up):
        ``host`` must come from ``cfg.effective_hosts`` — exactly the set
        this router was built from, so the lookup never misses."""
        if self._single is not None:
            return self._single
        return self._by_host[host]

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
