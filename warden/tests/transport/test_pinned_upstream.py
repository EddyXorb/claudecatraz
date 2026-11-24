"""The upstream client connects pinned hosts to their pinned address.

Real sockets, no mocks: the pin sits on private httpx/httpcore attributes, so
only an end-to-end connection proves an upgrade did not silently bypass it.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from warden.core.transport import upstream_client


@pytest.fixture
async def local_server() -> AsyncIterator[tuple[int, list[str]]]:
    hosts: list[str] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        hosts.extend(
            ln.split(":", 1)[1].strip()
            for ln in head.splitlines()
            if ln.lower().startswith("host:")
        )
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        yield port, hosts


async def test_pinned_host_reaches_pinned_address_with_its_own_host_header(
    local_server: tuple[int, list[str]],
) -> None:
    port, hosts = local_server
    client = upstream_client({"upstream.invalid": "127.0.0.1"})
    try:
        resp = await client.get(f"http://Upstream.Invalid:{port}/")
    finally:
        await client.aclose()
    assert resp.status_code == 200 and resp.text == "ok"
    assert hosts == [f"upstream.invalid:{port}"]


async def test_unpinned_host_resolves_normally(local_server: tuple[int, list[str]]) -> None:
    port, _ = local_server
    client = upstream_client({})
    try:
        resp = await client.get(f"http://127.0.0.1:{port}/")
    finally:
        await client.aclose()
    assert resp.status_code == 200
