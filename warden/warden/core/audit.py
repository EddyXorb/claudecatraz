"""JSONL audit log (W11, §6.8; F6, docs/design/architecture-generalization,
§02-befunde.md F6/F11, §06-migration.md Schritt 5/6): typed events, one
writer, O_APPEND, redaction-by-allowlist.

**F6 fix.** Every guard used to build its own near-identical dict by hand
(``git_proxy._audit``/``api_proxy._audit``). :class:`AuditEvent` is the one
typed constructor now — :meth:`core.guard.Guard.handle` builds exactly one on
every pipeline exit (allow or deny) from the envelope fields every guard
shares, plus whatever guard-specific extras :meth:`Guard.audit_fields`
supplies (F6: "Aufrufer konstruieren das Event typisiert").

**JSONL schema version history** (independent of the state DB's own
``user_version`` counter in :mod:`warden.core.state_migrations` — the two
schemas evolve on unrelated axes and unrelated numbers):

* **1** — the historical, feldlose format: no ``schema`` field on the line at
  all (every line written before Schritt 2).
* **2** (§06-migration.md Schritt 2) — introduces the ``schema`` field itself,
  gating the B3 fix (tag-push/branch-delete relogged from R2 to R4 — an
  audit-visible change to an *existing* field's value).
* **3** (§06-migration.md Schritt 6, F11) — the ``channel`` field is renamed
  to ``guard`` (values unchanged: still ``"git"``/``"api"``); paired with the
  state DB's own claude→agent table rename in the same migration step (both
  are one vocabulary shift, §03-guard-architektur.md §03.5). ``extra`` is
  still spread into the serialised dict exactly as the old
  ``**channel_fields``/``**guard_fields`` kwarg was: a guard that never passes
  a key (e.g. git never passes ``path``) leaves that key absent from the line
  entirely, while a guard that passes ``None`` explicitly (e.g. the REST
  guard's ``kind`` for an unmatched endpoint) serialises it as JSON ``null``.
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

# Audit-JSONL schema version — see the module docstring for the full version
# history (1 = feldlos, 2 = `schema` field + R2→R4, 3 = channel→guard).
# A reader (viewer, `catraz observe`) must keep accepting all of them: a
# missing `schema` field means version 1, and a `channel` field with no
# `guard` field means version <3 (compat window, §06.1).
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
    # §04.3 (docs/design/architecture-generalization/04-policy-erweiterbarkeit.md):
    # marks a decision made against a catalog entry a deployment's warden.toml
    # activated beyond the shipped default set (e.g. "config:branch.create").
    # Additive and optional — most events never carry it — so no
    # AUDIT_SCHEMA_VERSION bump: unlike the R2→R4 rename that earned version 2
    # (an audit-visible change to an *existing* field's value), a new,
    # absent-by-default field is exactly the kind of extension the
    # field-allowlist redaction was designed to admit without a version bump —
    # every existing reader (viewer.html, `catraz observe`) already renders
    # unknown/missing fields defensively.
    "enabled_via",
}


def redact(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k in _ALLOWED_FIELDS}


@dataclass(frozen=True)
class AuditEvent:
    """One decision, typed (F6). The envelope every guard shares is a typed
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
    """Thin, dict-returning compatibility facade over :class:`AuditEvent` (F6:
    "build_event darf zur dünnen Fassade werden"). New code constructs an
    :class:`AuditEvent` directly (:meth:`core.guard.Guard.handle` does); this
    remains for callers that still want the plain-dict shape (and for the
    tests pinning that shape down byte for byte).
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
