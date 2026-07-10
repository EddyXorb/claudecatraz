"""pkt-line parsing for the git Smart-HTTP receive-pack command section.

Transport-free and pure: turns the bytes that precede the PACK payload into a
list of RefCommand.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from typing import AsyncIterator, Optional

FLUSH = b"0000"


def pkt_line(data: bytes) -> bytes:
    """Encode a single pkt-line (4 hex length prefix incl. the prefix itself)."""
    return f"{len(data) + 4:04x}".encode() + data


@dataclass(frozen=True)
class RefCommand:
    """A single ref update from a receive-pack push: <old> <new> <ref>."""

    old: str
    new: str
    ref: str

    @property
    def is_create(self) -> bool:
        return _is_zero(self.old)

    @property
    def is_delete(self) -> bool:
        return _is_zero(self.new)


def _is_zero(oid: str) -> bool:
    # SHA-1 repos use 40 zeros, SHA-256 repos 64 — either way "all zeros".
    return len(oid) > 0 and oid.strip("0") == ""


def decompress_if_gzip(head: bytes) -> bytes:
    """Decompress the buffered head only if gzip-framed.

    Defensive: PACK bodies usually aren't gzip-coded. Only this parse copy is
    decompressed; the original body is forwarded untouched.
    """
    if head[:2] == b"\x1f\x8b":
        return gzip.decompress(head)
    return head


def parse_commands(head: bytes) -> list[RefCommand]:
    """Parse pkt-line ref commands until the first flush-pkt (0000).

    Expects enough buffered bytes to cover the command section (see
    read_until_flush).
    """
    head = decompress_if_gzip(head)
    cmds: list[RefCommand] = []
    i, n = 0, len(head)
    while i < n:
        if i + 4 > n:
            break
        length = int(head[i : i + 4], 16)
        if length == 0:  # flush-pkt → end of command section
            break
        line = head[i + 4 : i + length]
        # the first command carries capabilities after a NUL byte:
        #   "<old-oid> <new-oid> <ref>\0<caps>"
        line = line.split(b"\x00", 1)[0].rstrip(b"\n")
        parts = line.split(b" ", 2)
        if len(parts) == 3:
            old, new, ref = parts
            cmds.append(RefCommand(old.decode(), new.decode(), ref.decode()))
        i += length
    return cmds


def capabilities(head: bytes) -> set[str]:
    """Extract the capability tokens advertised on the first command line."""
    head = decompress_if_gzip(head)
    if len(head) < 4:
        return set()
    length = int(head[:4], 16)
    if length == 0:
        return set()
    line = head[4:length]
    if b"\x00" not in line:
        return set()
    caps = line.split(b"\x00", 1)[1].rstrip(b"\n")
    return {c.decode() for c in caps.split(b" ") if c}


def _find_command_end(buf: bytes | bytearray) -> Optional[int]:
    """Index just past the flush-pkt that ends the command section, or None.

    Returns None when more bytes are needed, or when the head isn't plain
    pkt-line framing (e.g. gzip) — the caller then buffers the whole body.
    """
    i, n = 0, len(buf)
    while True:
        if i + 4 > n:
            return None
        try:
            length = int(buf[i : i + 4], 16)
        except ValueError:
            return None
        if length == 0:
            return i + 4  # include the flush-pkt itself
        if length < 4:
            return None
        if i + length > n:
            return None
        i += length


async def read_until_flush(
    stream: AsyncIterator[bytes],
) -> tuple[bytes, AsyncIterator[bytes]]:
    """Buffer request body only up to the command-section flush.

    Returns (head, rest); rest re-yields the buffered remainder plus the
    untouched PACK stream, forwarded byte-for-byte without buffering it.
    """
    buf = bytearray()
    boundary: Optional[int] = None
    async for chunk in stream:
        buf.extend(chunk)
        boundary = _find_command_end(buf)
        if boundary is not None:
            break
    if boundary is None:
        boundary = len(buf)
    head = bytes(buf[:boundary])
    rest_prefix = bytes(buf[boundary:])

    async def rest() -> AsyncIterator[bytes]:
        if rest_prefix:
            yield rest_prefix
        async for chunk in stream:
            yield chunk

    return head, rest()
