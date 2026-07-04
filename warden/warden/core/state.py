"""Durable, fail-safe quota state: generic writes counter, reconcile lock, StateStore.

SQLite with WAL + ``synchronous=FULL``: every write-record commits *before* the upstream call.
State view is **locked** until a reconcile succeeds (never "empty = all free").

Kernel-owned: counters and fail-safe locking are resource-agnostic (M5), keyed by
``guard``/``kind`` strings. Module has no forge vocabulary — each guard owns its
own domain table on the shared connection: branches in
:class:`~warden.guards.git.state.BranchState`, MRs in
:class:`~warden.guards.gitlab_api.state.MrState`.

Schema versioning via SQLite's ``PRAGMA user_version`` — fail-closed (A9), not
migrated: see :meth:`StateStore._check_and_stamp_schema_version` for why a
mismatched version aborts rather than alters the existing shape.
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

CURRENT_SCHEMA_VERSION: Final[int] = 2
# v1 → v2 (§07 Punkt 8 follow-up): agent_branches/agent_mrs gained a `host`
# column (part of the primary key) for multi-target state-keying — no
# migration runs for it, an existing v1 DB is rejected instead (see
# _check_and_stamp_schema_version). A fresh DB is built at v2 directly.


class SchemaError(RuntimeError):
    """Raised when the state DB's schema version does not match this build's —
    fail-closed (see :meth:`StateStore._check_and_stamp_schema_version`)."""


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
    shared by core state and every guard's own domain state (the git guard's
    :class:`~warden.guards.git.state.BranchState`, the REST-API guard's
    :class:`~warden.guards.gitlab_api.state.MrState`) so they stay one writer on
    one file, never a second connection.

    Checks and stamps the schema version at connect time, before any table
    (core's or a domain's) is created — see
    :meth:`_check_and_stamp_schema_version` for the fail-closed rationale.
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
        """Fail-closed schema-version gate (A9), run before any ``CREATE TABLE``.

        Pre-1.0: no migration machinery — a DB is stamped at
        :data:`CURRENT_SCHEMA_VERSION` on first use (``CREATE TABLE IF NOT
        EXISTS`` builds the current shape directly). A DB stamped at *any*
        other version — newer **or** older — raises :class:`SchemaError`
        rather than run against a shape this build did not create.
        Older-not-just-newer matters because ``CREATE TABLE IF NOT EXISTS``
        only *creates* a table — it does not alter one that already exists
        under the old shape (§07 Punkt 8 follow-up: adding a column to an
        existing table, e.g. ``agent_branches``' ``host`` column, is exactly
        this case). A mismatched existing DB must therefore be rebuilt from
        scratch (delete the file) — consistent with "pre-1.0, state is
        disposable". A brand-new file (``user_version == 0``) is exempt:
        that is the ordinary bootstrap case, not a mismatch.
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
        successful reconcile; open_mrs/open_branches default to 0 — each guard
        fills its own domain count via its own ``state_view`` override (the
        git guard's :meth:`~warden.guards.git.guard.GitGuard.state_view`, the
        REST-API guard's :meth:`~warden.guards.gitlab_api.guard.ApiGuard.state_view`).
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
