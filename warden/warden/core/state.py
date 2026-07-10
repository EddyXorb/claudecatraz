"""Durable, fail-safe quota state: generic writes counter, reconcile lock, StateStore.

SQLite with WAL + synchronous=FULL: every write-record commits before the
upstream call. State view is locked until a reconcile succeeds (never
"empty = all free"). Kernel-owned and forge-agnostic; each guard owns its
own domain table on the shared connection.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Callable, Final, Iterable, Optional, Sequence

from .model import StateView

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "SchemaError",
    "State",
    "StateStore",
    "WINDOW_SECONDS",
]

CURRENT_SCHEMA_VERSION: Final[int] = 3
# Both past bumps added a host column so counters can be scoped per-endpoint;
# no migration runs — an older-versioned DB is rejected instead.


class SchemaError(RuntimeError):
    """Raised when the state DB's schema version does not match this build's —
    fail-closed (see StateStore._check_and_stamp_schema_version)."""


_CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS writes (
  id         INTEGER PRIMARY KEY,
  ts         REAL NOT NULL,
  guard      TEXT NOT NULL,
  host       TEXT NOT NULL DEFAULT '',
  kind       TEXT NOT NULL,
  ref_or_iid TEXT
);
CREATE INDEX IF NOT EXISTS idx_writes_ts ON writes(ts);
CREATE INDEX IF NOT EXISTS idx_writes_host_ts ON writes(host, ts);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

WINDOW_SECONDS = 3600


class StateStore:
    """The connection owner: one SQLite connection (WAL + synchronous=FULL),
    shared by core state and every guard's own domain state so they stay
    one writer on one file, never a second connection.
    """

    def __init__(self, db_path: str, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._check_and_stamp_schema_version()  # before any CREATE TABLE — see class docstring

    def _check_and_stamp_schema_version(self) -> None:
        """Fail-closed schema-version gate, run before any CREATE TABLE.

        Pre-1.0: no migration machinery. A DB stamped at any version other
        than 0 (fresh) or CURRENT_SCHEMA_VERSION raises SchemaError rather
        than run against a shape this build did not create or alter.
        """
        user_version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
        if user_version not in (0, CURRENT_SCHEMA_VERSION):
            raise SchemaError(
                f"state DB schema version {user_version} does not match this warden "
                f"build's schema ({CURRENT_SCHEMA_VERSION}) — refusing to start "
                "(fail-closed); delete the state DB to rebuild it fresh"
            )
        # user_version == 0 (fresh file) or == CURRENT_SCHEMA_VERSION: nothing to
        # lift — the caller's CREATE TABLE IF NOT EXISTS builds the current shape.
        self._db.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        self._db.commit()

    @property
    def clock(self) -> Callable[[], float]:
        return self._clock

    def execute(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Cursor:
        return self._db.execute(sql, params)

    def executemany(self, sql: str, seq: Iterable[Sequence[object]]) -> None:
        self._db.executemany(sql, seq)

    def executescript(self, script: str) -> None:
        self._db.executescript(script)
        self._db.commit()

    def commit(self) -> None:
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def schema_version(self) -> int:
        row = self._db.execute("PRAGMA user_version").fetchone()
        return int(row[0])


class State:
    """Core quota state: the writes counter and the reconcile lock, built
    on a StateStore. No branch/MR vocabulary — a guard with no domain
    state genuinely has no open branches/MRs, so view reports zero for
    those, never fabricating a forge concept.
    """

    def __init__(self, db_path: str, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._store = StateStore(db_path, clock=clock)
        self._store.executescript(_CORE_SCHEMA)

    @property
    def store(self) -> StateStore:
        """The shared connection, for a domain (e.g. the forge) to build its
        own tables on — never a second connection."""
        return self._store

    def close(self) -> None:
        self._store.close()

    def schema_version(self) -> int:
        return self._store.schema_version()

    # --- recording -------------------------------------------------------------
    def record_write(
        self, guard: str, host: str, kind: str, ref_or_iid: Optional[str] = None
    ) -> None:
        """Persist a write-record and fsync before the upstream call."""
        self._store.execute(
            "INSERT INTO writes (ts, guard, host, kind, ref_or_iid) VALUES (?, ?, ?, ?, ?)",
            (self._clock(), guard, host, kind, ref_or_iid),
        )
        self._store.commit()

    # --- views -----------------------------------------------------------------
    def writes_last_hour(self, host: str) -> int:
        """Rolling write-rate counter, scoped to host: a global count would
        let one busy endpoint exhaust every other endpoint's rate limit."""
        cutoff = self._clock() - WINDOW_SECONDS
        row = self._store.execute(
            "SELECT count(*) AS c FROM writes WHERE host=? AND ts > ?", (host, cutoff)
        ).fetchone()
        return int(row["c"])

    def is_reconciled(self, guard: str) -> bool:
        """Has guard reconciled its own domain at least once?

        Per guard, not global: one guard whose remote is unreachable
        fail-safe-locks only its own view, never the whole warden.
        """
        row = self._store.execute(
            "SELECT value FROM meta WHERE key = ?", (f"reconciled:{guard}",)
        ).fetchone()
        return row is not None

    def view(self, guard: str, host: str) -> StateView:
        """Core-only snapshot for the policy. Locked until guard first
        reconciles successfully; open_mrs/open_branches default to 0 — each
        guard fills its own domain count via its own state_view override.
        """
        if not self.is_reconciled(guard):
            return StateView(locked=True)
        return StateView(writes_last_hour=self.writes_last_hour(host), locked=False)

    # --- maintenance -----------------------------------------------------------
    def prune(self) -> None:
        cutoff = self._clock() - WINDOW_SECONDS
        self._store.execute("DELETE FROM writes WHERE ts < ?", (cutoff,))
        self._store.commit()

    def mark_reconciled(self, guard: str) -> None:
        """Record that guard reconciled its own domain (unlocks only that
        guard's view — see is_reconciled)."""
        self._store.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (f"reconciled:{guard}", str(self._clock())),
        )
        self._store.commit()
