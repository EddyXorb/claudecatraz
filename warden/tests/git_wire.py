"""Wire builders shared by tests that fake a git Smart-HTTP upstream."""

from __future__ import annotations

import httpx

from warden.guards.git.transport.pktline import FLUSH, pkt_line

_OID = "a" * 40


def advertisement(*refs: str) -> bytes:
    """A protocol-v0 upload-pack ref advertisement listing refs."""
    body = pkt_line(b"# service=git-upload-pack\n") + FLUSH
    for index, ref in enumerate(refs):
        caps = b"\x00multi_ack side-band-64k" if index == 0 else b""
        body += pkt_line(f"{_OID} {ref}".encode() + caps + b"\n")
    return body + FLUSH


def advertisement_response(*branches: str) -> httpx.Response:
    """A 200 advertisement response carrying refs/heads/<branch> for each branch."""
    return httpx.Response(
        200,
        content=advertisement(*(f"refs/heads/{b}" for b in branches)),
        headers={"content-type": "application/x-git-upload-pack-advertisement"},
    )


INFO_REFS = r".*\.git/info/refs\?service=git-upload-pack$"
