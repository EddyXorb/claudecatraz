"""Detached catraz workers and the lock files that keep them from racing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def lock_body(path: Path, stale_s: float) -> dict[str, Any] | None:
    """Body of a live lock, or None when absent or older than stale_s."""
    try:
        if time.time() - path.stat().st_mtime > stale_s:
            return None
        body = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def acquire(path: Path, stale_s: float) -> bool:
    """O_EXCL create (portable, no fcntl); a stale lock is broken once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if lock_body(path, stale_s) is not None:
                return False
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": os.getpid(), "started": time.time()}, f)
        return True
    return False


def wait_acquire(path: Path, stale_s: float, timeout_s: float) -> bool:
    """acquire, retrying until timeout_s; False when the holder outlasts it."""
    deadline = time.monotonic() + timeout_s
    while not acquire(path, stale_s):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


def release(path: Path) -> None:
    path.unlink(missing_ok=True)


def spawn(root: Path, args: list[str], log: Path) -> None:
    """Run `catraz <args> -C root` fully detached: own session/process group, no
    terminal, output to log. Best effort — the next trigger simply tries again."""
    pkg_parent = str(Path(__file__).resolve().parents[1])
    pythonpath = os.pathsep.join(p for p in (pkg_parent, os.environ.get("PYTHONPATH")) if p)
    env = dict(os.environ, PYTHONPATH=pythonpath)
    cmd = [sys.executable, "-m", "catraz.cli", *args, "-C", str(root)]
    kw: dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kw["start_new_session"] = True
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "w") as f:
            subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, env=env, **kw
            )
    except OSError:
        pass
