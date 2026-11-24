"""Host platform semantics — the one place that decides whether uids, file modes
and Unix sockets on this host mean anything to the containers."""

from __future__ import annotations

import os
import socket

# Absent from CPython on Windows: the warden's admin socket is then unreachable.
HAS_UNIX_SOCKETS = hasattr(socket, "AF_UNIX")


def host_uid() -> int | None:
    """The uid bind-mount ownership has to line up with, or None on a host with
    no POSIX uids. Windows drives carry no ownership: Docker Desktop presents
    host-created files as root:root 0777, so no value can match and DEV_UID
    only has to stay stable across runs."""
    getuid = getattr(os, "getuid", None)
    return getuid() if getuid is not None else None


def host_os() -> str:
    """The ownership contract handed to the container entrypoint: on `windows`
    the container owns /workspace ownership and repairs it after a DEV_UID
    change; on `posix` the files belong to the host user and the container
    never touches their ownership."""
    return "posix" if host_uid() is not None else "windows"


def unix_socket() -> socket.socket:
    """A stream Unix socket; OSError where the host has none, so callers treat
    it exactly like an unreachable warden."""
    if not HAS_UNIX_SOCKETS:
        raise OSError("Unix sockets are unavailable on this host")
    return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
