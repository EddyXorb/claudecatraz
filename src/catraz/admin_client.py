"""Minimal HTTP GET over the warden admin Unix socket.

Zero third-party dependencies; wraps http.client over a Unix socket instead
of TCP. Catraz never imports or executes warden's Python itself.
"""

from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path
from typing import Any


class AdminUnreachable(RuntimeError):
    """Admin socket missing, unresponsive, or answered without a 200 + JSON body."""


class _UdsHTTPConnection(http.client.HTTPConnection):
    def __init__(self, sock_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._sock_path = sock_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._sock_path)
        self.sock = sock


def admin_socket_path(root: Path) -> Path:
    return root / ".catraz" / "state" / "warden" / "run" / "admin.sock"


def get_json(root: Path, path: str, timeout: float = 3.0) -> Any:
    """GET path from the warden admin app over its Unix socket, parsed as JSON."""
    sock_path = admin_socket_path(root)
    if not sock_path.exists():
        raise AdminUnreachable(f"admin socket not found at {sock_path} — is the stack running?")
    conn = _UdsHTTPConnection(str(sock_path), timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
    except OSError as exc:
        raise AdminUnreachable(f"could not reach the warden admin socket: {exc}") from exc
    finally:
        conn.close()
    if resp.status != 200:
        raise AdminUnreachable(f"warden admin {path} returned HTTP {resp.status}")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise AdminUnreachable(f"warden admin {path} returned invalid JSON: {exc}") from exc
