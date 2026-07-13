"""Host ownership semantics — the one place that decides whether uids and file
modes on this host mean anything to the containers."""

from __future__ import annotations

import os


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
