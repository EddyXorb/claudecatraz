"""pktline.py robustness: malformed / truncated framing must degrade gracefully, never crash the proxy."""

from __future__ import annotations

from warden.guards.git.transport.pktline import (
    FLUSH,
    _find_command_end,
    capabilities,
    parse_commands,
    pkt_line,
    read_until_flush,
)


async def _aiter(*chunks):
    for c in chunks:
        yield c


ZERO = "0" * 40
SHA = "1" * 40


def _cmd_line(old, new, ref, caps=b"") -> bytes:
    payload = f"{old} {new} {ref}".encode()
    if caps:
        payload += b"\x00" + caps
    return pkt_line(payload + b"\n")


# --- capabilities --------------------------------------------------------------
def test_capabilities_empty_on_short_or_flush_head():
    assert capabilities(b"") == set()  # < 4 bytes
    assert capabilities(FLUSH) == set()  # flush-pkt only


def test_capabilities_empty_when_first_line_has_no_nul():
    head = _cmd_line(ZERO, SHA, "refs/heads/claude/x")  # no caps section
    assert capabilities(head) == set()


def test_capabilities_parses_tokens_after_nul():
    head = _cmd_line(ZERO, SHA, "refs/heads/claude/x", caps=b"report-status side-band-64k")
    assert capabilities(head) == {"report-status", "side-band-64k"}


# --- parse_commands ------------------------------------------------------------
def test_parse_commands_ignores_trailing_partial_prefix():
    # A complete command followed by a stray 2-byte fragment (not a full 4-hex
    # length prefix) must yield the one command and stop, not raise.
    head = _cmd_line(ZERO, SHA, "refs/heads/claude/x") + b"00"
    cmds = parse_commands(head)
    assert len(cmds) == 1 and cmds[0].ref == "refs/heads/claude/x"


# --- _find_command_end ---------------------------------------------------------
def test_find_command_end_needs_more_bytes():
    assert _find_command_end(b"00") is None  # fewer than 4 length bytes


def test_find_command_end_rejects_non_hex_length():
    assert _find_command_end(b"zzzz rest") is None


def test_find_command_end_none_on_reserved_short_length():
    assert _find_command_end(b"0003") is None  # 0 < len < 4 is invalid framing


def test_find_command_end_none_when_body_incomplete():
    assert _find_command_end(b"0030abc") is None  # claims 0x30 bytes, only a few present


def test_find_command_end_points_past_the_flush():
    line = pkt_line(b"x")
    buf = line + FLUSH + b"PACK....."
    end = _find_command_end(buf)
    assert end == len(line) + len(FLUSH)
    assert buf[:end].endswith(FLUSH)


# --- read_until_flush ----------------------------------------------------------
async def test_read_until_flush_splits_head_from_pack():
    head_bytes = _cmd_line(ZERO, SHA, "refs/heads/claude/x") + FLUSH
    stream = _aiter(head_bytes, b"PACKDATA1", b"PACKDATA2")
    head, rest = await read_until_flush(stream)
    assert head == head_bytes  # command section, up to and including the flush
    assert b"".join([c async for c in rest]) == b"PACKDATA1PACKDATA2"  # untouched pack


async def test_read_until_flush_buffers_whole_body_when_no_flush():
    # gzip / non-pkt-line framing has no discoverable flush → buffer it all and
    # forward it byte-for-byte, still SHA-preserving.
    blob = b"\x1f\x8b not real pkt-line framing"
    head, rest = await read_until_flush(_aiter(blob))
    assert head == blob
    assert [c async for c in rest] == []
