"""What the running infra services enforce, compared with the config on disk.

Each infra supervisor records the fingerprint of the inputs it last decided
on. Recomputing that fingerprint from the host files tells applied, rejected,
not-yet-picked-up and out-of-sync apart exactly, without asking docker.
"""

from __future__ import annotations

import hashlib
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from catraz.ui import Out

# Order matters: it must match the supervisor's own input order.
INPUTS: dict[str, tuple[str, ...]] = {
    "gitlab-warden": ("config/warden.toml", "secrets/read_tokens", "secrets/write_tokens"),
    "forward-proxy": ("config/squid.conf", "config/allowlist.txt"),
}
STATUS_FILES = {
    "gitlab-warden": "state/warden/run/config-status",
    "forward-proxy": "logs/squid/config-status",
}
# Supervisors poll every second and debounce one poll; beyond this a mismatch
# means the container cannot see the file.
PICKUP_GRACE_S = 10.0

APPLIED = "applied"
REJECTED = "rejected"
PENDING = "pending"
OUT_OF_SYNC = "out-of-sync"
UNKNOWN = "unknown"


def fingerprint(paths: list[Path]) -> str:
    """sha256 over one per-file sha256 line each; "-" for an unreadable file."""
    outer = hashlib.sha256()
    for path in paths:
        try:
            line = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            line = "-"
        outer.update(f"{line}\n".encode())
    return outer.hexdigest()


def input_paths(root: Path, service: str) -> list[Path]:
    return [root / ".catraz" / rel for rel in INPUTS[service]]


def read_status(path: Path) -> dict[str, str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    status = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    return status if "state" in status and "fingerprint" in status else None


@dataclass(frozen=True)
class LiveState:
    service: str
    state: str
    detail: str = ""


def live_state(root: Path, service: str, *, now: Callable[[], float] = time.time) -> LiveState:
    """Classify one service; UNKNOWN when its image writes no status file."""
    status = read_status(root / ".catraz" / STATUS_FILES[service])
    if status is None:
        return LiveState(service, UNKNOWN)
    paths = input_paths(root, service)
    if status["fingerprint"] == fingerprint(paths):
        if status["state"] == APPLIED:
            return LiveState(service, APPLIED)
        return LiveState(service, REJECTED, status.get("detail", ""))
    newest = max((p.stat().st_mtime for p in paths if p.exists()), default=0.0)
    if now() - newest < PICKUP_GRACE_S:
        return LiveState(service, PENDING)
    return LiveState(service, OUT_OF_SYNC)


def report(root: Path, out: Out, services: list[str] | None = None) -> bool:
    """Print rejected and out-of-sync services loudly; True if any is rejected."""
    rejected = False
    for service in services if services is not None else sorted(INPUTS):
        ls = live_state(root, service)
        if ls.state == REJECTED:
            rejected = True
            out.err(
                f"{service}: config REJECTED — all traffic through it is denied until "
                f"fixed: {ls.detail}"
            )
        elif ls.state == OUT_OF_SYNC:
            out.warn(
                f"{service}: running config differs from .catraz — run `catraz reload` "
                "to recreate it"
            )
    return rejected


def warden_running(root: Path) -> bool:
    """True when a warden answers on its host-side admin socket."""
    sock_path = root / ".catraz" / "state" / "warden" / "run" / "admin.sock"
    s = socket.socket(socket.AF_UNIX)
    try:
        s.settimeout(1.0)
        s.connect(str(sock_path))
    except OSError:
        return False
    finally:
        s.close()
    return True


def wait_settled(
    root: Path,
    service: str,
    *,
    timeout: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> LiveState:
    """Poll until the service has decided on the current files (or timeout)."""
    deadline = now() + timeout
    while True:
        ls = live_state(root, service, now=now)
        if ls.state != PENDING or now() >= deadline:
            return ls
        sleep(0.25)
