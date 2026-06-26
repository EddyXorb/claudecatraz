"""Durable, fail-safe quota state (W8, §6.11).

SQLite with WAL + ``synchronous=FULL``; every write-record commits *before* the
upstream call so a crash never loses the hourly counter. If the state cannot be
reconstructed, the view is **locked** ("limit reached") until a reconcile
succeeds — never "empty = 0 used = all free".
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional

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


class State:
    def __init__(self, db_path: str, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

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
