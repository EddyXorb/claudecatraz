"""Durable, fail-safe quota state (W8, §6.11): the generic writes counter and
the reconcile lock, plus :class:`StateStore` — the connection owner every
domain builds its own tables on.

SQLite with WAL + ``synchronous=FULL``; every write-record commits *before*
the upstream call so a crash never loses the hourly counter. If the state
cannot be reconstructed, the view is **locked** ("limit reached") until a
reconcile succeeds — never "empty = 0 used = all free".

Kernel-owned (§03.3): the counters and their fail-safe locking are a
resource-agnostic concept (M5) any guard's quotas can share, keyed by the
``guard``/``kind`` strings a guard passes to :meth:`State.record_write` — this
module has no forge vocabulary of its own (no branch/MR tables; those live in
the forge domain's own :class:`~warden.guards.gitlab.state.ForgeState`, built
on the :class:`StateStore` this module exposes).

**Schema versioning** (§06-migration.md Schritt 2, F11): the DB carries its
schema version in SQLite's own ``PRAGMA user_version`` — a single integer slot
SQLite reserves exactly for this, always present (defaults to 0), so it needs
no bootstrap table of its own and survives even a database that predates this
concept. A small ``meta`` key/value table already exists here for
``last_reconcile``, but that is *application* state written mid-session; the
schema version is a *structural* fact checked once at connect time before any
table is touched, which is what ``user_version`` is for — reusing ``meta``
would conflate the two. The versioned migrations themselves (including the
Schritt-6 claude→agent/channel→guard rename) live in
:mod:`warden.core.state_migrations`, re-exported below for callers that only
need the version constants/exception, not the migration internals.
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
    """The connection owner (the "Persistenz-Werkzeug", §E): one SQLite
    connection (WAL + ``synchronous=FULL``), shared by core state and every
    domain's own state (e.g. the forge's :class:`~warden.guards.gitlab.state.ForgeState`)
    so they stay one writer on one file, never a second connection.

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
    """Core quota state (W8, §6.11): the ``writes`` counter and the reconcile
    lock, built on a :class:`StateStore`. No branch/MR vocabulary — a guard
    with no domain state genuinely has no open branches/MRs, so
    :meth:`view` reports zero for those, never fabricating a forge concept.
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
        """Persist a write-record and fsync *before* the upstream call (§6.11)."""
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
        row = self._store.execute(
            "SELECT value FROM meta WHERE key='last_reconcile'"
        ).fetchone()
        return row is not None

    def view(self) -> StateView:
        """Core-only snapshot for the policy. Locked until the first
        successful reconcile; open_mrs/open_branches default to 0 — a domain
        (e.g. :class:`~warden.guards.gitlab.forge.GitlabForge`) fills those
        via its own :meth:`~warden.guards.gitlab.forge.GitlabForge.state_view`.
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
