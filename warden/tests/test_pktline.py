"""pkt-line parser tests (W14, §8): good/bad prefix, delete=null-oid, multi-ref, gzip head."""

from __future__ import annotations

import gzip

from warden.guards.git.pktline import (
    FLUSH,
    capabilities,
    parse_commands,
    pkt_line,
    read_until_flush,
)

ZERO = "0" * 40
SHA1 = "1" * 40
SHA2 = "2" * 40
CAPS = "report-status side-band-64k atomic"


def make_receive_pack(commands, caps=CAPS, pack=b"PACK\x00\x00\x00\x02rest-of-packfile"):
    out = b""
    for idx, (old, new, ref) in enumerate(commands):
        payload = f"{old} {new} {ref}"
        if idx == 0:
            payload += "\x00" + caps
        payload += "\n"
        out += pkt_line(payload.encode())
    return out + FLUSH + pack


def test_parse_single_command():
    body = make_receive_pack([(ZERO, SHA1, "refs/heads/claude/x")])
    cmds = parse_commands(body)
    assert len(cmds) == 1
    assert cmds[0].ref == "refs/heads/claude/x"
    assert cmds[0].is_create and not cmds[0].is_delete


def test_parse_multi_ref():
    body = make_receive_pack(
        [(ZERO, SHA1, "refs/heads/claude/a"), (SHA1, SHA2, "refs/heads/claude/b")]
    )
    cmds = parse_commands(body)
    assert [c.ref for c in cmds] == ["refs/heads/claude/a", "refs/heads/claude/b"]


def test_parse_delete_is_null_oid():
    body = make_receive_pack([(SHA1, ZERO, "refs/heads/claude/x")])
    cmd = parse_commands(body)[0]
    assert cmd.is_delete and not cmd.is_create


def test_capabilities_extracted():
    body = make_receive_pack([(ZERO, SHA1, "refs/heads/claude/x")])
    caps = capabilities(body)
    assert "report-status" in caps and "side-band-64k" in caps


def test_parse_gzip_head():
    body = make_receive_pack([(ZERO, SHA1, "refs/heads/claude/x")])
    gz = gzip.compress(body)
    cmds = parse_commands(gz)
    assert cmds[0].ref == "refs/heads/claude/x"


async def test_read_until_flush_splits_head_and_pack():
    body = make_receive_pack([(ZERO, SHA1, "refs/heads/claude/x")])

    async def stream():
        # deliver in small chunks to exercise incremental buffering
        for i in range(0, len(body), 7):
            yield body[i : i + 7]

    head, rest = await read_until_flush(stream())
    assert head.endswith(FLUSH)
    cmds = parse_commands(head)
    assert cmds[0].ref == "refs/heads/claude/x"

    # the remainder must reconstruct the body byte-for-byte (SHA-preserving)
    reassembled = head
    async for chunk in rest:
        reassembled += chunk
    assert reassembled == body
