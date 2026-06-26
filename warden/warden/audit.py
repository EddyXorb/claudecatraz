"""JSONL audit log (W11, §6.8): one writer, O_APPEND, redaction-by-allowlist.

A single asyncio task drains a queue and appends one complete JSON line per
decision. Tokens / Authorization are **never** logged (allowlist of fields, not
a blocklist). Logging failures never block the policy — the decision still
stands and the error goes to stderr (fail-safe).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Only these keys are ever serialised — anything else (tokens, headers, bodies)
# is dropped by construction.
_ALLOWED_FIELDS = {
    "ts",
    "channel",
    "correlation_id",
    "method",
    "path",
    "project",
    "decision",
    "rule",
    "reason",
    "refs",
    "kind",
    "upstream_status",
    "latency_ms",
    "bytes",
    "open_mrs",
    "open_branches",
    "writes_last_hour",
}


def redact(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k in _ALLOWED_FIELDS}


class AuditLog:
    def __init__(self, path: str) -> None:
        self._path = path
        if path not in ("-", "/dev/stderr"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._queue: asyncio.Queue[Optional[dict[str, Any]]] = asyncio.Queue()
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            await self._queue.put(None)
            await self._task
            self._task = None

    def log(self, entry: dict[str, Any]) -> None:
        """Enqueue a decision. Non-blocking; safe to call from any handler."""
        record = redact({"ts": time.time(), **entry})
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:  # pragma: no cover - unbounded queue
            print("warden: audit queue full, dropping entry", file=sys.stderr)

    async def _run(self) -> None:
        while True:
            record = await self._queue.get()
            if record is None:
                return
            line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
            try:
                self._write(line)
            except Exception as exc:  # fail-safe: never block policy on logging
                print(f"warden: audit write failed: {exc}", file=sys.stderr)

    def _write(self, line: str) -> None:
        if self._path in ("-", "/dev/stderr"):
            sys.stderr.write(line)
            return
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
