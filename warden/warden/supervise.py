"""PID-1 supervisor: runs one warden child per validated config.

Each child builds its Config once; a changed config is applied by replacing
the child. An invalid config stops the child and starts none (deny-all) until
a valid one appears. The outcome is published in a status file for the host.
"""

from __future__ import annotations

import hashlib
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Mapping, Optional, Protocol, Sequence

from .core.config import ConfigError
from .core.config_load import DEFAULT_TOML_PATH, from_env

log = logging.getLogger("warden.supervise")

# The child's fail-closed startup abort (ConfigError/SchemaError) exit code.
CONFIG_ABORT_EXIT = 2
STOP_TIMEOUT_S = 10.0


class Child(Protocol):
    def poll(self) -> Optional[int]: ...
    def send_signal(self, sig: int) -> None: ...
    def wait(self, timeout: Optional[float] = None) -> int: ...
    def kill(self) -> None: ...


def config_inputs(env: Mapping[str, str]) -> list[Optional[Path]]:
    """warden.toml, read_tokens, write_tokens — fixed order, None when unset.
    The host CLI fingerprints the same three files in the same order."""
    toml = env.get("WARDEN_CONFIG_PATH") or DEFAULT_TOML_PATH
    paths: list[Optional[Path]] = [Path(toml)]
    for name in ("READ_TOKENS_FILE", "WRITE_TOKENS_FILE"):
        value = env.get(name)
        paths.append(Path(value) if value else None)
    return paths


def fingerprint(paths: Sequence[Optional[Path]]) -> str:
    """sha256 over one per-file sha256 line each; "-" for an unreadable file."""
    outer = hashlib.sha256()
    for path in paths:
        try:
            line = hashlib.sha256(path.read_bytes()).hexdigest() if path else "-"
        except OSError:
            line = "-"
        outer.update(f"{line}\n".encode())
    return outer.hexdigest()


def validate(env: Mapping[str, str]) -> Optional[str]:
    """None when the config loads exactly as a fresh warden would load it."""
    try:
        from_env(env)
    except ConfigError as exc:
        return str(exc)
    return None


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
        log.error("cannot write config status %s: %s", path, exc)


def _spawn_warden(env: Mapping[str, str]) -> Child:
    return subprocess.Popen([sys.executable, "-m", "warden"], env=dict(env))


class Supervisor:
    """Poll-driven state machine; tick() is one poll, run() loops it."""

    def __init__(
        self,
        env: Mapping[str, str],
        *,
        spawn: Callable[[Mapping[str, str]], Child] = _spawn_warden,
        status_path: Optional[Path] = None,
        stop_timeout_s: float = STOP_TIMEOUT_S,
    ) -> None:
        self.env = env
        self.spawn = spawn
        self.status_path = status_path
        self.stop_timeout_s = stop_timeout_s
        self.child: Optional[Child] = None
        self.applied_fp = ""
        self.pending_fp = ""

    def _fingerprint(self) -> str:
        return fingerprint(config_inputs(self.env))

    def stop_child(self) -> None:
        child, self.child = self.child, None
        if child is None or child.poll() is not None:
            return
        child.send_signal(signal.SIGTERM)
        try:
            child.wait(timeout=self.stop_timeout_s)
        except subprocess.TimeoutExpired:
            log.warning("warden did not stop within %.0fs — killing it", self.stop_timeout_s)
            child.kill()
            child.wait()

    def _reject(self, fp: str, reason: str) -> None:
        log.error("CONFIG REJECTED — warden denies all agent traffic until fixed: %s", reason)
        write_status(self.status_path, "rejected", fp, reason)

    def apply(self, fp: str) -> None:
        """Stop the running child, then start one only if the config validates."""
        self.applied_fp = fp
        self.stop_child()
        reason = validate(self.env)
        if reason is not None:
            self._reject(fp, reason)
            return
        self.child = self.spawn(self.env)
        write_status(self.status_path, "applied", fp)
        log.info("config applied (%s)", fp[:12])

    def start(self) -> None:
        self.apply(self._fingerprint())

    def tick(self) -> Optional[int]:
        """One poll. Returns an exit code when the supervisor must end."""
        if self.child is not None:
            rc = self.child.poll()
            if rc == CONFIG_ABORT_EXIT:
                self.child = None
                self._reject(self.applied_fp, "warden exited on a config/schema error (see log)")
            elif rc is not None:
                self.child = None
                return rc if rc > 0 else 1
        fp = self._fingerprint()
        if fp == self.applied_fp:
            self.pending_fp = ""
        elif fp != self.pending_fp:
            self.pending_fp = fp  # apply only once it is stable for a full poll
        else:
            self.pending_fp = ""
            log.info("config change detected — re-validating")
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


def main(env: Optional[Mapping[str, str]] = None) -> int:
    env = env if env is not None else dict(os.environ)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    status = env.get("CONFIG_STATUS_FILE")
    try:
        poll_s = float(env.get("CONFIG_POLL_S") or 1.0)
    except ValueError:
        poll_s = 1.0
    if poll_s <= 0:
        poll_s = 1.0
    sup = Supervisor(env, status_path=Path(status) if status else None)
    return sup.run(poll_s)
