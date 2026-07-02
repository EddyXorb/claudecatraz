"""Durable, fail-safe quota state (W8, §6.11).

SQLite with WAL + ``synchronous=FULL``; every write-record commits *before* the
upstream call so a crash never loses the hourly counter. If the state cannot be
reconstructed, the view is **locked** ("limit reached") until a reconcile
succeeds — never "empty = 0 used = all free".

**Schema versioning** (§06-migration.md Schritt 2, F11 precondition): the DB
carries its schema version in SQLite's own ``PRAGMA user_version`` — a single
integer slot SQLite reserves exactly for this, always present (defaults to 0),
so it needs no bootstrap table of its own and survives even a database that
predates this concept. A small ``meta`` key/value table already exists here
for ``last_reconcile``, but that is *application* state written mid-session;
the schema version is a *structural* fact checked once at connect time before
any table is touched, which is what ``user_version`` is for — reusing ``meta``
would conflate the two. See :func:`_run_migrations`.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final, Optional

from .model import StateView

_SCHEMA = """
CREATE TABLE IF NOT EXISTS writes (
  id         INTEGER PRIMARY KEY,
  ts         REAL NOT NULL,
  channel    TEXT NOT NULL,
  kind       TEXT NOT NULL,
  ref_or_iid TEXT
);
CREATE INDEX IF NOT EXISTS idx_writes_ts ON writes(ts);

CREATE TABLE IF NOT EXISTS claude_branches (
  project TEXT, ref TEXT, created REAL,
  PRIMARY KEY (project, ref)
);
CREATE TABLE IF NOT EXISTS claude_mrs (
  project TEXT, iid INTEGER, state TEXT, created REAL,
  PRIMARY KEY (project, iid)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

WINDOW_SECONDS = 3600


class SchemaError(RuntimeError):
    """Raised when the state DB's schema version is newer than this build
    understands — fail-closed (A9): a downgrade must never silently run
    against a shape it does not fully know, so it refuses to start."""


@dataclass(frozen=True)
class Migration:
    """One versioned step: ``apply`` carries whatever SQL lifts the DB from
    ``version - 1`` to ``version`` (renames, column adds, backfills, …). A
    migration is a *named function*, not inline SQL in the runner, so each
    step stays independently readable and testable."""

    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _stamp_schema_version(conn: sqlite3.Connection) -> None:
    """Version 1 → 2 (§06-migration.md Schritt 2): no table changes.

    Version 1 is the historical, implicit shape (``claude_branches``/
    ``claude_mrs``, no version marker at all). This step only introduces the
    version marker itself — the table renames B3's sibling findings call for
    (claude→agent, F11) are Schritt 6; this migration's only job is to prove
    the runner can carry a step, so that later migration is a small diff here,
    not new infrastructure.
    """
    # Intentionally empty — see docstring. The runner stamps PRAGMA user_version.


# Legacy DBs (pre-dating this module) are implicitly version 1: they already
# have ``claude_branches``/``claude_mrs`` but no ``user_version`` marker.
BASE_SCHEMA_VERSION: Final[int] = 1

MIGRATIONS: tuple[Migration, ...] = (Migration(2, "stamp_schema_version", _stamp_schema_version),)

CURRENT_SCHEMA_VERSION: Final[int] = MIGRATIONS[-1].version if MIGRATIONS else BASE_SCHEMA_VERSION


def _has_legacy_tables(conn: sqlite3.Connection) -> bool:
    """True iff pre-migration tables already exist (a real legacy DB, not a
    brand-new file) — the signal that distinguishes "nothing to migrate,
    already fresh" from "unversioned, needs lifting to current"."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='claude_branches'"
    ).fetchone()
    return row is not None


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Lift the DB to :data:`CURRENT_SCHEMA_VERSION`, fail-closed on a future one.

    Must run *before* :data:`_SCHEMA` creates any table (``CREATE TABLE IF NOT
    EXISTS``), otherwise a legacy DB and a brand-new one look identical to
    :func:`_has_legacy_tables`. A brand-new file has nothing to lift — it is
    created straight at :data:`CURRENT_SCHEMA_VERSION`. A legacy, unversioned
    file (``user_version`` 0 but tables already present) starts at
    :data:`BASE_SCHEMA_VERSION` and runs every migration above it, in order,
    without losing a row (no migration in this step drops or renames data).
    """
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if user_version > CURRENT_SCHEMA_VERSION:
        raise SchemaError(
            f"state DB schema version {user_version} is newer than this warden "
            f"build supports ({CURRENT_SCHEMA_VERSION}) — refusing to start (fail-closed)"
        )
    if user_version == 0:
        current = BASE_SCHEMA_VERSION if _has_legacy_tables(conn) else CURRENT_SCHEMA_VERSION
    else:
        current = user_version
    for migration in MIGRATIONS:
        if migration.version > current:
            migration.apply(conn)
            current = migration.version
    conn.execute(f"PRAGMA user_version = {current}")
    conn.commit()


class State:
    def __init__(self, db_path: str, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        _run_migrations(self._db)  # before schema creation (see _run_migrations)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def schema_version(self) -> int:
        row = self._db.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    # --- recording -------------------------------------------------------------
    def record_write(self, channel: str, kind: str, ref_or_iid: Optional[str] = None) -> None:
        """Persist a write-record and fsync *before* the upstream call (§6.11)."""
        self._db.execute(
            "INSERT INTO writes (ts, channel, kind, ref_or_iid) VALUES (?, ?, ?, ?)",
            (self._clock(), channel, kind, ref_or_iid),
        )
        self._db.commit()

    def add_branch(self, project: str, ref: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO claude_branches (project, ref, created) VALUES (?, ?, ?)",
            (project, ref, self._clock()),
        )
        self._db.commit()

    def upsert_mr(self, project: str, iid: int, state: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO claude_mrs (project, iid, state, created) VALUES "
            "(?, ?, ?, COALESCE((SELECT created FROM claude_mrs WHERE project=? AND iid=?), ?))",
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
        row = self._db.execute("SELECT count(*) AS c FROM claude_branches").fetchone()
        return int(row["c"])

    def open_mrs(self) -> int:
        row = self._db.execute(
            "SELECT count(*) AS c FROM claude_mrs WHERE state='opened'"
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
        self._db.execute("DELETE FROM claude_branches WHERE project=?", (project,))
        now = self._clock()
        self._db.executemany(
            "INSERT OR REPLACE INTO claude_branches (project, ref, created) VALUES (?, ?, ?)",
            [(project, r, now) for r in refs],
        )
        self._db.commit()

    def replace_mrs(self, project: str, mrs: list[tuple[int, str]]) -> None:
        self._db.execute("DELETE FROM claude_mrs WHERE project=?", (project,))
        now = self._clock()
        self._db.executemany(
            "INSERT OR REPLACE INTO claude_mrs (project, iid, state, created) VALUES (?, ?, ?, ?)",
            [(project, iid, st, now) for iid, st in mrs],
        )
        self._db.commit()

    def mark_reconciled(self) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_reconcile', ?)",
            (str(self._clock()),),
        )
        self._db.commit()
