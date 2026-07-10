"""End-to-end git test: a real `git push` through the Warden against a throwaway
upstream `git http-backend`, verifying accept/reject and SHA-equality. Skipped
when `git` is unavailable.
"""

from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from warden.app import create_app
from warden.context import build_context
from warden.core.audit import AuditLog
from warden.core.config import Config, GitEndpoint, HostCredentials
from warden.core.state import State

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("openssl") is None,
    reason="git/openssl not installed",
)


def _make_self_signed_cert(tmp_path) -> tuple[str, str]:
    """Ephemeral self-signed cert+key: the fake upstream must terminate real
    TLS, since the Warden never speaks a raw, unencrypted scheme upstream."""
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return str(cert), str(key)


# --- a minimal git Smart-HTTP upstream backed by `git http-backend` ------------
def _make_backend_handler(project_root: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # silence
            pass

        def _read_body(self):
            te = self.headers.get("Transfer-Encoding", "").lower()
            if "chunked" in te:
                data = b""
                while True:
                    size_line = self.rfile.readline().strip()
                    if not size_line:
                        continue
                    size = int(size_line.split(b";")[0], 16)
                    if size == 0:
                        self.rfile.readline()  # trailing CRLF
                        break
                    data += self.rfile.read(size)
                    self.rfile.readline()  # CRLF after each chunk
                return data
            length = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(length) if length else b""

        def _serve(self):
            body = self._read_body()
            path, _, query = self.path.partition("?")
            env = {
                "GIT_PROJECT_ROOT": project_root,
                "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "REQUEST_METHOD": self.command,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(len(body)),
                "REMOTE_USER": "tester",
                "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
            }
            proc = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True)
            raw = proc.stdout
            header_blob, _, payload = raw.partition(b"\r\n\r\n")
            status = 200
            headers = []
            for line in header_blob.split(b"\r\n"):
                if not line:
                    continue
                key, _, value = line.partition(b": ")
                if key.lower() == b"status":
                    status = int(value.split(b" ")[0])
                else:
                    headers.append((key.decode(), value.decode()))
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _serve
        do_POST = _serve

    return Handler


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port: int, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"port {port} not up")


def _git(cwd, *args, check=True):
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(cwd),
        "PATH": __import__("os").environ["PATH"],
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=check
    )


def _run_e2e(tmp_path, *, actions=None):
    """Build a real upstream git http-backend plus a real Warden in front of
    it. actions overrides the endpoint's effective actions (default:
    inherit the built-in default)."""
    import uvicorn

    # 1. throwaway bare upstream repo
    root = tmp_path / "upstream"
    root.mkdir()
    repo = root / "repo.git"
    subprocess.run(["git", "init", "--bare", "-q", str(repo)], check=True)
    # allow pushing to the checked-out (default) branch of a bare repo is fine
    subprocess.run(["git", "-C", str(repo), "config", "http.receivepack", "true"], check=True)

    backend_port = _free_port()
    backend = ThreadingHTTPServer(("127.0.0.1", backend_port), _make_backend_handler(str(root)))
    # The backend must terminate real TLS — wrap its listening socket with a
    # throwaway self-signed cert before it starts accepting.
    cert, key = _make_self_signed_cert(tmp_path)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certfile=cert, keyfile=key)
    backend.socket = tls.wrap_socket(backend.socket, server_side=True)
    threading.Thread(target=backend.serve_forever, daemon=True).start()

    # 2. warden in front, pointing at the upstream, `type="plain"` since
    # `git http-backend` has no GitLab REST surface at all.
    backend_host = f"127.0.0.1:{backend_port}"
    cfg = Config(
        branch_prefixes=("claude/",),
        allowed_projects=("repo",),
        state_db_path=str(tmp_path / "state.db"),
        git_endpoints=(GitEndpoint(host=backend_host, type="plain", actions=actions),),
        git_credentials={
            Config.normalize_host(backend_host): HostCredentials(read_token="r", write_token="w")
        },
    )
    state = State(cfg.state_db_path)
    state.mark_reconciled("git")
    state.mark_reconciled("api")
    # The backend's cert is self-signed (a throwaway test double) —
    # verify=False here only, never a default in production.
    upstream_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0), verify=False)
    ctx = build_context(cfg, state, AuditLog("-"), client=upstream_client)
    warden_port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(ctx), host="127.0.0.1", port=warden_port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_port(warden_port)
    _wait_port(backend_port)

    remote = f"http://127.0.0.1:{warden_port}/git/repo.git"
    try:
        yield tmp_path, remote
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        backend.shutdown()


@pytest.fixture
def e2e(tmp_path):
    yield from _run_e2e(tmp_path)


@pytest.fixture
def e2e_no_branch_push(tmp_path):
    # repo.branch.create stays on so the first push succeeds; repo.branch.push
    # is off, so an update to that branch is denied, naming the action.
    yield from _run_e2e(tmp_path, actions=("repo.read", "repo.branch.create"))


def _seed_clone(tmp_path, remote, branch):
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "checkout", "-q", "-b", branch)
    (work / "file.txt").write_text("hello")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "initial")
    _git(work, "remote", "add", "origin", remote)
    sha = _git(work, "rev-parse", "HEAD").stdout.strip()
    return work, sha


def test_push_allowed_branch_is_sha_preserving(e2e):
    tmp_path, remote = e2e
    work, local_sha = _seed_clone(tmp_path, remote, "claude/feature")

    res = _git(work, "push", "-q", "origin", "claude/feature", check=False)
    assert res.returncode == 0, res.stderr

    # The server stored exactly the local commit — same SHA (host clone stays coherent).
    server_sha = _git(work, "ls-remote", "origin", "refs/heads/claude/feature").stdout.split()[0]
    assert server_sha == local_sha


def test_push_forbidden_branch_is_rejected(e2e):
    tmp_path, remote = e2e
    work, _ = _seed_clone(tmp_path, remote, "main")

    res = _git(work, "push", "origin", "main", check=False)
    assert res.returncode != 0
    assert "outside allowed prefixes" in (res.stderr + res.stdout)


def test_push_update_denied_when_branch_push_disabled_names_the_action(e2e_no_branch_push):
    # The create succeeds (repo.branch.create is on); the follow-up update to
    # the same branch is denied per-ref, naming the specific disabled action.
    tmp_path, remote = e2e_no_branch_push
    work, _ = _seed_clone(tmp_path, remote, "claude/feature")

    created = _git(work, "push", "-q", "origin", "claude/feature", check=False)
    assert created.returncode == 0, created.stderr

    (work / "file.txt").write_text("updated")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "second")
    updated = _git(work, "push", "origin", "claude/feature", check=False)
    assert updated.returncode != 0
    assert "action repo.branch.push not enabled" in (updated.stderr + updated.stdout)
