"""Durable, fail-safe quota state: generic writes counter, reconcile lock, StateStore.

SQLite with WAL + ``synchronous=FULL``: every write-record commits *before* the upstream call.
State view is **locked** until a reconcile succeeds (never "empty = all free").

Kernel-owned: counters and fail-safe locking are resource-agnostic (M5), keyed by
``guard``/``kind`` strings. Module has no forge vocabulary (branch/MR tables live
in forge's own :class:`~warden.guards.gitlab.state.ForgeState`).

Schema versioning via SQLite's ``PRAGMA user_version``. Migrations in
:mod:`warden.core.state_migrations`.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from .model import StateView
from .state_migrations import (
    BASE_SCHEMA_VERSION,
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
    SchemaError,
    run_migrations,
)

__all__ = [
    "BASE_SCHEMA_VERSION",
    "CURRENT_SCHEMA_VERSION",
    "MIGRATIONS",
    "SchemaError",
    "State",
    "StateStore",
    "WINDOW_SECONDS",
]

_CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS writes (
  id         INTEGER PRIMARY KEY,
  ts         REAL NOT NULL,
  guard      TEXT NOT NULL,
  kind       TEXT NOT NULL,
  ref_or_iid TEXT
);
CREATE INDEX IF NOT EXISTS idx_writes_ts ON writes(ts);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

WINDOW_SECONDS = 3600


class StateStore:
    """The connection owner: one SQLite connection (WAL + ``synchronous=FULL``),
    shared by core state and every domain's own state (e.g. the forge's
    :class:`~warden.guards.gitlab.state.ForgeState`) so they stay one writer on
    one file, never a second connection.

    Runs the historical schema migrations at connect time, before any table
    (core's or a domain's) is created — a legacy ``claude_*`` DB must already
    be lifted to current names by the time a domain's own
    ``CREATE TABLE IF NOT EXISTS`` runs, or that statement would create a
    second, empty table alongside the legacy one instead of a no-op.
    """

    def __init__(self, db_path: str, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        run_migrations(self._db)  # before any CREATE TABLE — see class docstring

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
    """Core quota state: the ``writes`` counter and the reconcile lock, built
    on a :class:`StateStore`. No branch/MR vocabulary — a guard with no domain
    state genuinely has no open branches/MRs, so :meth:`view` reports zero for
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
    def record_write(self, guard: str, kind: str, ref_or_iid: Optional[str] = None) -> None:
        """Persist a write-record and fsync *before* the upstream call."""
        self._store.execute(
            "INSERT INTO writes (ts, guard, kind, ref_or_iid) VALUES (?, ?, ?, ?)",
            (self._clock(), guard, kind, ref_or_iid),
        )
        self._store.commit()

    # --- views -----------------------------------------------------------------
    def writes_last_hour(self) -> int:
        cutoff = self._clock() - WINDOW_SECONDS
        row = self._store.execute(
            "SELECT count(*) AS c FROM writes WHERE ts > ?", (cutoff,)
        ).fetchone()
        return int(row["c"])

    def is_reconciled(self) -> bool:
        row = self._store.execute("SELECT value FROM meta WHERE key='last_reconcile'").fetchone()
        return row is not None

    def view(self) -> StateView:
        """Core-only snapshot for the policy. Locked until the first
        successful reconcile; open_mrs/open_branches default to 0 — a domain
        (e.g. :class:`~warden.guards.gitlab.forge.GitForge`) fills those
        via its own :meth:`~warden.guards.gitlab.forge.GitForge.state_view`.
        """
        if not self.is_reconciled():
            return StateView(locked=True)
        return StateView(writes_last_hour=self.writes_last_hour(), locked=False)

    # --- maintenance -----------------------------------------------------------
    def prune(self) -> None:
        cutoff = self._clock() - WINDOW_SECONDS
        self._store.execute("DELETE FROM writes WHERE ts < ?", (cutoff,))
        self._store.commit()

    def mark_reconciled(self) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_reconcile', ?)",
            (str(self._clock()),),
        )
        self._store.commit()
