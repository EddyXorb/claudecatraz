import socket
import threading
import urllib.request
from pathlib import Path

import pytest

from catraz import cli

pytestmark = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"), reason="Unix sockets unavailable on this host"
)


def test_audit_web_forwards_to_uds(tmp_path: Path) -> None:
    sockdir = tmp_path / ".catraz/state/warden/run"
    sockdir.mkdir(parents=True)
    sp = sockdir / "admin.sock"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(sp))
    srv.listen()

    def serve() -> None:
        c, _ = srv.accept()
        c.recv(1024)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        c.close()

    threading.Thread(target=serve, daemon=True).start()
    import socketserver

    h = type("H", (cli._UdsProxy,), {"sock_path": str(sp)})
    fwd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), h)
    threading.Thread(target=fwd.serve_forever, daemon=True).start()
    body = urllib.request.urlopen(f"http://127.0.0.1:{fwd.server_address[1]}/").read()
    assert body == b"ok"
    fwd.shutdown()
