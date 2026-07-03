"""JSONL audit log: typed events, one writer, O_APPEND, redaction-by-allowlist.

:class:`AuditEvent` is the one typed constructor — :meth:`core.guard.Guard.handle`
builds exactly one on every pipeline exit from shared envelope fields plus guard-specific extras.

**JSONL schema version history** (independent of state DB's own counter):

* **1** — no ``schema`` field.
* **2** — introduces ``schema`` field; tag-push/branch-delete relogged from R2 to R4.
* **3** — ``channel`` field renamed to ``guard`` (values unchanged: ``"git"``/``"api"``).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Mapping, Optional

from .model import Decision, StateView

# Audit-JSONL schema version — see module docstring for version history.
# A reader (viewer, `catraz observe`) must keep accepting all of them:
# missing `schema` field means version 1, `channel` without `guard` means version <3.
AUDIT_SCHEMA_VERSION: Final[int] = 3

# Only these keys are ever serialised — anything else (tokens, headers, bodies)
# is dropped by construction.
_ALLOWED_FIELDS = {
    "ts",
    "schema",
    "guard",
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
    "open_mrs",
    "open_branches",
    "writes_last_hour",
    # Marks a decision against a catalog entry activated beyond the default set.
    # Additive and optional; no AUDIT_SCHEMA_VERSION bump needed (field-allowlist
    # redaction was designed to admit extensions without version bump).
    "enabled_via",
}


def redact(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k in _ALLOWED_FIELDS}


@dataclass(frozen=True)
class AuditEvent:
    """One decision, fully typed. The envelope every guard shares is a typed
    attribute; ``extra`` carries whatever additional fields a specific guard
    supplies (REST: ``path``/``kind``/``enabled_via``; git: ``refs``) — see
    the module docstring for why this stays a passthrough mapping rather than
    a fixed set of optional attributes: "present with value None" and
    "absent" must both stay expressible, exactly as the old
    ``**guard_fields`` kwargs allow.
    """

    guard: str
    correlation_id: str
    method: str
    project: str
    decision: Decision
    state: StateView
    started: float
    upstream_status: Optional[int]
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": AUDIT_SCHEMA_VERSION,
            "guard": self.guard,
            "correlation_id": self.correlation_id,
            "method": self.method,
            "project": self.project,
            "decision": "allow" if self.decision.allow else "deny",
            "rule": self.decision.rule,
            "reason": self.decision.reason,
            "upstream_status": self.upstream_status,
            "latency_ms": round((time.monotonic() - self.started) * 1000, 1),
            "open_mrs": self.state.open_mrs,
            "open_branches": self.state.open_branches,
            "writes_last_hour": self.state.writes_last_hour,
            **self.extra,
        }


def build_event(
    *,
    guard: str,
    correlation_id: str,
    method: str,
    project: str,
    decision: Decision,
    state: StateView,
    started: float,
    upstream_status: Optional[int],
    **guard_fields: Any,
) -> dict[str, Any]:
    """Dict-returning compatibility facade over :class:`AuditEvent`. New code
    constructs an :class:`AuditEvent` directly; this remains for callers that
    still want the plain-dict shape.
    """
    return AuditEvent(
        guard=guard,
        correlation_id=correlation_id,
        method=method,
        project=project,
        decision=decision,
        state=state,
        started=started,
        upstream_status=upstream_status,
        extra=guard_fields,
    ).to_dict()


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

    def log(self, entry: AuditEvent | dict[str, Any]) -> None:
        """Enqueue a decision. Non-blocking; safe to call from any handler.

        Accepts a typed :class:`AuditEvent` (the kernel's own calls) or a
        plain dict (older/direct callers, e.g. tests exercising redaction in
        isolation) — both end up through the same redact-and-stamp path.
        """
        raw = entry.to_dict() if isinstance(entry, AuditEvent) else entry
        record = redact({"ts": time.time(), **raw})
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
