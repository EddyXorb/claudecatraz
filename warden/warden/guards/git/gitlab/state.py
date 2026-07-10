"""The REST-API guard's own quota table: agent_mrs, in the same SQLite
file as core state via the shared store, never a second connection.
Keyed by (host, project, iid) with a per-endpoint open_mrs count.
"""

from __future__ import annotations

from ....core.state import StateStore

_MR_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_mrs (
  host TEXT NOT NULL DEFAULT '', project TEXT, iid INTEGER, state TEXT, created REAL,
  PRIMARY KEY (host, project, iid)
);
"""


class MrState:
    def __init__(self, store: StateStore) -> None:
        self._store = store
        self._store.executescript(_MR_SCHEMA)

    def upsert_mr(self, host: str, project: str, iid: int, state: str) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO agent_mrs (host, project, iid, state, created) VALUES "
            "(?, ?, ?, ?, COALESCE("
            "(SELECT created FROM agent_mrs WHERE host=? AND project=? AND iid=?), ?))",
            (host, project, iid, state, host, project, iid, self._store.clock()),
        )
        self._store.commit()

    def open_mrs(self, host: str) -> int:
        row = self._store.execute(
            "SELECT count(*) AS c FROM agent_mrs WHERE host=? AND state='opened'", (host,)
        ).fetchone()
        return int(row["c"])

    def replace_mrs(self, host: str, project: str, mrs: list[tuple[int, str]]) -> None:
        self._store.execute("DELETE FROM agent_mrs WHERE host=? AND project=?", (host, project))
        now = self._store.clock()
        self._store.executemany(
            "INSERT OR REPLACE INTO agent_mrs (host, project, iid, state, created) "
            "VALUES (?, ?, ?, ?, ?)",
            [(host, project, iid, st, now) for iid, st in mrs],
        )
        self._store.commit()
