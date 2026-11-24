"""Keep running infra on the current config without a manual `catraz reload`.

Every command runs a file-only trigger; when the inputs that shape the rendered
compose config changed, or a supervisor reports out of sync, a detached
`catraz reload --auto` renders, compares the config hashes and recreates.
"""

from __future__ import annotations

import contextlib
import hashlib
import time
from collections.abc import Iterator
from pathlib import Path

from catraz import __version__, background, compose, live_config, paths
from catraz.admin_client import admin_socket_path
from catraz.ui import Out

LOCK_STALE_S = 15 * 60
# An out-of-sync state that a recreate did not cure must not respawn per command.
RETRY_AFTER_S = 60


def lock_path(root: Path) -> Path:
    return root / ".catraz" / "state" / "reconcile.lock"


@contextlib.contextmanager
def infra_lock(root: Path, wait_s: float = 300) -> Iterator[bool]:
    """Serialises infra (re)creates across catraz processes; yields whether held."""
    path = lock_path(root)
    held = background.wait_acquire(path, LOCK_STALE_S, wait_s)
    try:
        yield held
    finally:
        if held:
            background.release(path)


def _stamp_path(root: Path) -> Path:
    return root / ".catraz" / "state" / "reconcile.stamp"


def notice_path(root: Path) -> Path:
    return root / ".catraz" / "state" / "reconcile.notice"


def log_path(root: Path) -> Path:
    return root / ".catraz" / "logs" / "reconcile.log"


def inputs_fingerprint(root: Path) -> str:
    """Everything a render reads besides the asset-independent DNS answers."""
    h = hashlib.sha256(__version__.encode())
    h.update((paths.asset_root() / ".extracted").read_bytes())
    for rel in (".env", "compose.override.yml"):
        try:
            h.update((root / ".catraz" / rel).read_bytes())
        except OSError:
            h.update(b"-")
    h.update(" ".join(compose._git_endpoint_hosts(root)).encode())
    return h.hexdigest()


def _stack_up(root: Path) -> bool:
    return admin_socket_path(root).exists()


def trigger(root: Path, out: Out) -> None:
    """Report the last background outcome, then spawn a reconcile if needed."""
    notice = notice_path(root)
    if notice.exists():
        for line in notice.read_text(encoding="utf-8").splitlines():
            out.info(f"• reconcile: {line}")
        notice.unlink(missing_ok=True)
    if not _stack_up(root) or background.lock_body(lock_path(root), LOCK_STALE_S) is not None:
        return
    stamp = _stamp_path(root)
    fp = inputs_fingerprint(root)
    try:
        last_fp, last_at = stamp.read_text(encoding="utf-8"), stamp.stat().st_mtime
    except OSError:
        last_fp, last_at = "", 0.0
    out_of_sync = any(
        live_config.live_state(root, s).state == live_config.OUT_OF_SYNC
        for s in compose.INFRA_SERVICES
    )
    if fp == last_fp and not (out_of_sync and time.time() - last_at > RETRY_AFTER_S):
        return
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(fp, encoding="utf-8")
    background.spawn(root, ["reload", "--auto"], log_path(root))
    out.info(
        f"• checking infra against the current config in the background (log: {log_path(root)})"
    )
