"""PID 1 of the forward-proxy: runs squid and applies config edits live.

A changed file is applied once it is unchanged for a full poll: valid
(`squid -k parse`) -> SIGHUP reconfigure; invalid -> squid stopped, no egress.
Each decision lands in $CONFIG_STATUS_FILE for the host CLI.
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Mapping, Optional, Protocol, Sequence

DEFAULT_WATCH = "/etc/squid/squid.conf /etc/squid/allowlist.txt"
STOP_TIMEOUT_S = 10.0


class Child(Protocol):
    def poll(self) -> Optional[int]: ...
    def send_signal(self, sig: int) -> None: ...
    def wait(self, timeout: Optional[float] = None) -> int: ...
    def kill(self) -> None: ...


def say(msg: str) -> None:
    print(f"squid-supervise: {msg}", file=sys.stderr, flush=True)


def fingerprint(paths: Sequence[Path]) -> str:
    """sha256 over one per-file sha256 line each; "-" for an unreadable file.
    The host CLI fingerprints the same files in the same order."""
    outer = hashlib.sha256()
    for path in paths:
        try:
            line = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            line = "-"
        outer.update(f"{line}\n".encode())
    return outer.hexdigest()


def parse_reason(output: str) -> str:
    """The last FATAL/ERROR line of `squid -k parse`, else its last line."""
    lines = [ln for ln in output.splitlines() if ln.strip()]
    errors = [ln for ln in lines if "FATAL" in ln or "ERROR" in ln]
    picked = errors or lines
    return picked[-1] if picked else "squid -k parse failed"


def write_status(path: Optional[Path], state: str, fp: str, detail: str = "") -> None:
    """Atomically replace the status file; a write failure is logged, not fatal."""
    if path is None:
        return
    one_line = " ".join(detail.split())
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(f"state={state}\nfingerprint={fp}\ndetail={one_line}\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        say(f"cannot write {path}: {exc}")


class Squid:
    """The squid binary: config check and a foreground server process."""

    def __init__(self, binary: str, conf: str) -> None:
        self.binary = binary
        self.conf = conf

    def check(self) -> Optional[str]:
        """None when `squid -k parse` accepts the config, else the reason."""
        try:
            r = subprocess.run(
                [self.binary, "-k", "parse", "-f", self.conf],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            return str(exc)
        return None if r.returncode == 0 else parse_reason(r.stdout)

    def spawn(self) -> Child:
        return subprocess.Popen([self.binary, "-N", "-d1", "-f", self.conf])


class Supervisor:
    """Poll-driven state machine; tick() is one poll, run() loops it."""

    def __init__(
        self,
        watch: Sequence[Path],
        *,
        check: Callable[[], Optional[str]],
        spawn: Callable[[], Child],
        status_path: Optional[Path] = None,
        stop_timeout_s: float = STOP_TIMEOUT_S,
    ) -> None:
        self.watch = list(watch)
        self.check = check
        self.spawn = spawn
        self.status_path = status_path
        self.stop_timeout_s = stop_timeout_s
        self.child: Optional[Child] = None
        self.applied_fp = ""
        self.pending_fp = ""

    def stop_child(self) -> None:
        child, self.child = self.child, None
        if child is None or child.poll() is not None:
            return
        child.send_signal(signal.SIGTERM)
        try:
            child.wait(timeout=self.stop_timeout_s)
        except subprocess.TimeoutExpired:
            say(f"squid did not stop within {self.stop_timeout_s:.0f}s — killing it")
            child.kill()
            child.wait()

    def apply(self, fp: str) -> None:
        """Valid: reconfigure the running squid or start one. Invalid: stop it."""
        self.applied_fp = fp
        reason = self.check()
        if reason is not None:
            self.stop_child()
            write_status(self.status_path, "rejected", fp, reason)
            say(f"CONFIG REJECTED — all egress denied until fixed: {reason}")
            return
        if self.child is not None:
            self.child.send_signal(signal.SIGHUP)
        else:
            self.child = self.spawn()
        write_status(self.status_path, "applied", fp)
        say(f"config applied ({fp[:12]})")

    def start(self) -> None:
        self.apply(fingerprint(self.watch))

    def tick(self) -> Optional[int]:
        """One poll. Returns an exit code when the supervisor must end."""
        if self.child is not None:
            rc = self.child.poll()
            if rc is not None:
                self.child = None
                say(f"squid exited ({rc})")
                return rc if rc > 0 else 1
        fp = fingerprint(self.watch)
        if fp == self.applied_fp:
            self.pending_fp = ""
        elif fp != self.pending_fp:
            self.pending_fp = fp  # apply only once it is stable for a full poll
        else:
            self.pending_fp = ""
            self.apply(fp)
        return None

    def run(self, poll_s: float, sleep: Callable[[float], None] = time.sleep) -> int:
        stopping: list[int] = []

        def _on_signal(signum: int, _frame: object) -> None:
            stopping.append(signum)

        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)
        self.start()
        try:
            while not stopping:
                sleep(poll_s)
                if stopping:
                    break
                rc = self.tick()
                if rc is not None:
                    return rc
            return 0
        finally:
            self.stop_child()


def poll_interval(env: Mapping[str, str]) -> float:
    try:
        poll_s = float(env.get("CONFIG_POLL_S") or 1.0)
    except ValueError:
        return 1.0
    return poll_s if poll_s > 0 else 1.0


def main(env: Optional[Mapping[str, str]] = None) -> int:
    env = env if env is not None else dict(os.environ)
    squid = Squid(env.get("SQUID_BIN") or "squid", env.get("SQUID_CONF") or "/etc/squid/squid.conf")
    watch = [Path(p) for p in (env.get("SQUID_WATCH") or DEFAULT_WATCH).split()]
    status = env.get("CONFIG_STATUS_FILE") or "/var/log/squid/config-status"
    sup = Supervisor(watch, check=squid.check, spawn=squid.spawn, status_path=Path(status))
    return sup.run(poll_interval(env))


if __name__ == "__main__":
    sys.exit(main())
