"""catraz.admin_client (§04.3): minimal HTTP-over-Unix-socket GET, zero deps."""
from __future__ import annotations

import http.server
import socketserver
import threading
from pathlib import Path
from typing import Iterator

import pytest

from catraz.admin_client import AdminUnreachable, admin_socket_path, get_json

UdsFixture = tuple[Path, "_UdsServer"]


def test_admin_socket_path_layout(tmp_path: Path) -> None:
    assert admin_socket_path(tmp_path) == tmp_path / ".catraz" / "state" / "warden" / "run" / "admin.sock"


def test_get_json_raises_when_socket_missing(tmp_path: Path) -> None:
    with pytest.raises(AdminUnreachable, match="admin socket not found"):
        get_json(tmp_path, "/policy")


class _UdsServer(socketserver.UnixStreamServer):
    allow_reuse_address = True


class _JsonHandler(http.server.BaseHTTPRequestHandler):
    body = b'{"catalog": [{"id": "mr.create", "active": true}]}'
    status = 200

    def log_message(self, *a: object) -> None:  # silence test output
        pass

    def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)


@pytest.fixture
def uds_server(tmp_path: Path) -> Iterator[UdsFixture]:
    sock_path = tmp_path / ".catraz" / "state" / "warden" / "run" / "admin.sock"
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    srv = _UdsServer(str(sock_path), _JsonHandler, bind_and_activate=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield tmp_path, srv
    srv.shutdown()
    srv.server_close()


def test_get_json_returns_parsed_body(uds_server: UdsFixture) -> None:
    root, _srv = uds_server
    body = get_json(root, "/policy")
    assert body["catalog"][0]["id"] == "mr.create"


def test_get_json_raises_on_non_200(uds_server: UdsFixture) -> None:
    root, srv = uds_server
    srv.RequestHandlerClass.status = 500  # type: ignore[attr-defined]
    try:
        with pytest.raises(AdminUnreachable, match="HTTP 500"):
            get_json(root, "/policy")
    finally:
        srv.RequestHandlerClass.status = 200  # type: ignore[attr-defined]


def test_get_json_raises_on_invalid_json(uds_server: UdsFixture) -> None:
    root, srv = uds_server
    srv.RequestHandlerClass.body = b"not json"  # type: ignore[attr-defined]
    try:
        with pytest.raises(AdminUnreachable, match="invalid JSON"):
            get_json(root, "/policy")
    finally:
        srv.RequestHandlerClass.body = b'{"catalog": []}'  # type: ignore[attr-defined]
