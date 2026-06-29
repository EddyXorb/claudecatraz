"""httpx client to gitlab.com: token injection + project mapping (W9.2, W4).

Read- vs. write-token chosen per :class:`~warden.policy.Decision`. REST uses the
``PRIVATE-TOKEN`` header; git Smart-HTTP uses HTTP-Basic ``oauth2:<token>``.
"""

from __future__ import annotations

import base64
from typing import AsyncIterator, Optional
from urllib.parse import quote

import httpx
from starlette.responses import StreamingResponse

from .config import Config, normalize_project
from .model import TokenKind

# Hop-by-hop headers that must not be forwarded verbatim.
_DROP_REQUEST_HEADERS = {"host", "authorization", "private-token", "content-length", "connection", "accept-encoding"}
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


def stream_upstream(resp: httpx.Response) -> StreamingResponse:
    """Relay a streamed upstream response to the client, closing it when done.

    Shared by the REST and git proxies so the body is never buffered (W6/W7).
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
