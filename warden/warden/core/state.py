"""Durable, fail-safe quota state (W8, §6.11).

SQLite with WAL + ``synchronous=FULL``; every write-record commits *before* the
upstream call so a crash never loses the hourly counter. If the state cannot be
reconstructed, the view is **locked** ("limit reached") until a reconcile
succeeds — never "empty = 0 used = all free".

Kernel-owned (§03.3): the counters and their fail-safe locking are a
resource-agnostic concept (M5) any guard's quotas can share, keyed by the
``guard``/``kind`` strings a guard passes to :meth:`State.record_write` — this
module has no GitLab/git vocabulary of its own. ``agent_branches``/
``agent_mrs`` are named for what they track (the agent's own namespace-scoped
branches/MRs, §03.5), not for a specific guard.

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
from typing import Callable, Optional

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
    "WINDOW_SECONDS",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS writes (
  id         INTEGER PRIMARY KEY,
  ts         REAL NOT NULL,
  guard      TEXT NOT NULL,
  kind       TEXT NOT NULL,
  ref_or_iid TEXT
);
CREATE INDEX IF NOT EXISTS idx_writes_ts ON writes(ts);

CREATE TABLE IF NOT EXISTS agent_branches (
  project TEXT, ref TEXT, created REAL,
  PRIMARY KEY (project, ref)
);
CREATE TABLE IF NOT EXISTS agent_mrs (
  project TEXT, iid INTEGER, state TEXT, created REAL,
  PRIMARY KEY (project, iid)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

WINDOW_SECONDS = 3600


class State:
    def __init__(self, db_path: str, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        run_migrations(self._db)  # before schema creation (see state_migrations.run_migrations)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def schema_version(self) -> int:
        row = self._db.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    # --- recording -------------------------------------------------------------
    def record_write(self, guard: str, kind: str, ref_or_iid: Optional[str] = None) -> None:
        """Persist a write-record and fsync *before* the upstream call (§6.11)."""
        self._db.execute(
            "INSERT INTO writes (ts, guard, kind, ref_or_iid) VALUES (?, ?, ?, ?)",
            (self._clock(), guard, kind, ref_or_iid),
        )
        self._db.commit()

    def add_branch(self, project: str, ref: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO agent_branches (project, ref, created) VALUES (?, ?, ?)",
            (project, ref, self._clock()),
        )
        self._db.commit()

    def upsert_mr(self, project: str, iid: int, state: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO agent_mrs (project, iid, state, created) VALUES "
            "(?, ?, ?, COALESCE((SELECT created FROM agent_mrs WHERE project=? AND iid=?), ?))",
            (project, iid, state, project, iid, self._clock()),
        )
        self._db.commit()

    # --- views -----------------------------------------------------------------
    def writes_last_hour(self) -> int:
        cutoff = self._clock() - WINDOW_SECONDS
        row = self._db.execute(
            "SELECT count(*) AS c FROM writes WHERE ts > ?", (cutoff,)
        ).fetchone()
        return int(row["c"])

    def open_branches(self) -> int:
        row = self._db.execute("SELECT count(*) AS c FROM agent_branches").fetchone()
        return int(row["c"])

    def open_mrs(self) -> int:
        row = self._db.execute(
            "SELECT count(*) AS c FROM agent_mrs WHERE state='opened'"
        ).fetchone()
        return int(row["c"])

    def is_reconciled(self) -> bool:
        row = self._db.execute("SELECT value FROM meta WHERE key='last_reconcile'").fetchone()
        return row is not None

    def view(self) -> StateView:
        """Snapshot for the policy. Locked until the first successful reconcile."""
        if not self.is_reconciled():
            return StateView(locked=True)
        return StateView(
            open_mrs=self.open_mrs(),
            open_branches=self.open_branches(),
            writes_last_hour=self.writes_last_hour(),
            locked=False,
        )

    # --- maintenance -----------------------------------------------------------
    def prune(self) -> None:
        cutoff = self._clock() - WINDOW_SECONDS
        self._db.execute("DELETE FROM writes WHERE ts < ?", (cutoff,))
        self._db.commit()

    def replace_branches(self, project: str, refs: list[str]) -> None:
        self._db.execute("DELETE FROM agent_branches WHERE project=?", (project,))
        now = self._clock()
        self._db.executemany(
            "INSERT OR REPLACE INTO agent_branches (project, ref, created) VALUES (?, ?, ?)",
            [(project, r, now) for r in refs],
        )
        self._db.commit()

    def replace_mrs(self, project: str, mrs: list[tuple[int, str]]) -> None:
        self._db.execute("DELETE FROM agent_mrs WHERE project=?", (project,))
        now = self._clock()
        self._db.executemany(
            "INSERT OR REPLACE INTO agent_mrs (project, iid, state, created) VALUES (?, ?, ?, ?)",
            [(project, iid, st, now) for iid, st in mrs],
        )
        self._db.commit()

    def mark_reconciled(self) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_reconcile', ?)",
            (str(self._clock()),),
        )
        self._db.commit()
