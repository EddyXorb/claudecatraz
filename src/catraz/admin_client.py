"""Minimal HTTP GET over the warden admin Unix socket (§04.3).

Zero third-party dependencies by design (catraz.pyproject declares none) —
this is a thin wrapper around ``http.client`` pointed at a Unix socket
instead of TCP. Used by ``catraz doctor``'s endpoint-catalog section and
``catraz allow-endpoint`` to query the running warden's read-only ``/policy``
route (warden/warden/app.py) instead of guessing at catalog contents
client-side (A2: catraz never imports or executes warden's Python — it only
ships it as a container asset, see ``[tool.hatch.build.targets.wheel.force-include]``
in pyproject.toml).
"""

from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path
from typing import Any


class AdminUnreachable(RuntimeError):
    """The admin socket is missing, unresponsive, or answered with something
    that isn't a 200 + valid JSON body. Callers degrade to an offline mode."""


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
    """GET ``path`` from the warden admin app over its Unix socket, parsed
    as JSON. Raises :class:`AdminUnreachable` on anything short of a clean
    200 + JSON response — the socket missing (stack down) is the common case.
    """
    sock_path = admin_socket_path(root)
    if not sock_path.exists():
        raise AdminUnreachable(
            f"admin socket not found at {sock_path} — is the stack running?"
        )
    conn = _UdsHTTPConnection(str(sock_path), timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
    except OSError as exc:
        raise AdminUnreachable(
            f"could not reach the warden admin socket: {exc}"
        ) from exc
    finally:
        conn.close()
    if resp.status != 200:
        raise AdminUnreachable(f"warden admin {path} returned HTTP {resp.status}")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise AdminUnreachable(
            f"warden admin {path} returned invalid JSON: {exc}"
        ) from exc
