"""Observability commands: logs, audit."""

from __future__ import annotations

import argparse
import contextlib
import socket
import socketserver
import subprocess
import threading
import webbrowser
from pathlib import Path

from catraz.errors import EXIT_OK, EXIT_GENERAL
from catraz.compose import run as compose_run, resolve_service, _rc
from catraz import compose
from catraz.ui import Out


def _tail_files(d: Path, glob: str, label: str, args: argparse.Namespace, out: Out) -> int:
    files = sorted(d.glob(glob)) if d.exists() else []
    if not files:
        out.warn(f"no {label} in {d}")
        return EXIT_OK
    cmd = ["tail"]
    if args.follow:
        cmd.append("-f")
    cmd += ["-n", str(args.tail), *map(str, files)]
    subprocess.run(cmd)
    return EXIT_OK


def _tail_audit(root: Path, args: argparse.Namespace, out: Out) -> int:
    return _tail_files(root / ".catraz/logs/warden", "*.jsonl", "audit logs", args, out)


class _UdsProxy(socketserver.BaseRequestHandler):
    sock_path = ""  # per-instance via type(...)

    def handle(self) -> None:
        with socket.socket(socket.AF_UNIX) as up:
            up.connect(self.sock_path)

            def fwd(a: socket.socket, b: socket.socket) -> None:
                try:
                    while d := a.recv(65536):
                        b.sendall(d)
                except OSError:
                    pass
                finally:
                    with contextlib.suppress(OSError):
                        b.shutdown(socket.SHUT_WR)

            t = threading.Thread(target=fwd, args=(self.request, up), daemon=True)
            t.start()
            fwd(up, self.request)
            t.join()


def cmd_logs(root: Path, args: argparse.Namespace, out: Out) -> int:
    if args.audit:
        return _tail_audit(root, args, out)
    # The agent runs as an ephemeral `run --rm` one-off, not a daemon, so
    # `docker compose logs` never captures it — tail its file transcripts instead.
    if args.service and resolve_service(args.service) == compose.SERVICES["agent"]:
        return _tail_files(root / ".catraz/logs/agent", "*.log", "agent logs", args, out)
    log_args = ["logs"]
    if args.follow:
        log_args.append("-f")
    log_args += ["--tail", str(args.tail)]
    if args.service:
        log_args.append(resolve_service(args.service))
    prefix = compose.prepare(root, render=False)
    r = compose_run(root, log_args, prefix=prefix, check=False)
    return _rc(r)


def cmd_ps(root: Path, args: argparse.Namespace, out: Out) -> int:
    # all=True so in-flight `run --rm` one-offs (hidden from a plain `ps`) are visible,
    # not just the `up -d` daemon. Always EXIT_OK — this is a query, not a health gate.
    prefix = compose.prepare(root, render=False)
    rows = compose.compose_ps(root, prefix=prefix, all=True)
    agents = [r for r in rows if r.get("Service") == compose.SERVICES["agent"]]
    if not agents:
        out.info("No active agent containers.")
        return EXIT_OK
    out.head("Agent containers")
    for r in agents:
        name = r.get("Name", "?")
        state = r.get("State", "?")
        # Color by state directly — no health gate: the one-off agent has no
        # healthcheck, so _row_ready would wrongly down-rank it.
        badge = out.green(state) if state == "running" else out.yellow(state)
        extra = f"  {r.get('Status', '')}"
        print(f"  {name}  {badge}{extra}")
    return EXIT_OK


def cmd_audit(root: Path, args: argparse.Namespace, out: Out) -> int:
    sock = root / ".catraz/state/warden/run/admin.sock"
    if not args.web:
        return _tail_audit(root, args, out)  # existing JSONL tail
    if not sock.exists():
        out.err("audit socket not found — run `catraz run` first")
        return EXIT_GENERAL
    handler = type("H", (_UdsProxy,), {"sock_path": str(sock)})
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)  # ephemeral port
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    out.info(f"audit viewer: {url}  (Ctrl-C to stop)")
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return EXIT_OK
