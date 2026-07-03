"""The REST-API guard's own quota table: ``agent_mrs``, living in the same
SQLite file as :mod:`warden.core.state` via the shared
:class:`~warden.core.state.StateStore` — never a second connection.

Folded here from the now-dissolved ``guards.gitlab.state.ForgeState``
(§07 Punkt 6, step 5): branch tracking lives in the git guard's own
:mod:`warden.guards.git.state`; this table is the REST-API guard's MR-quota
domain only.
"""

from __future__ import annotations

from ...core.state import StateStore

_MR_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_mrs (
  project TEXT, iid INTEGER, state TEXT, created REAL,
  PRIMARY KEY (project, iid)
);
"""


class MrState:
    def __init__(self, store: StateStore) -> None:
        self._store = store
        self._store.executescript(_MR_SCHEMA)

    def upsert_mr(self, project: str, iid: int, state: str) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO agent_mrs (project, iid, state, created) VALUES "
            "(?, ?, ?, COALESCE((SELECT created FROM agent_mrs WHERE project=? AND iid=?), ?))",
            (project, iid, state, project, iid, self._store.clock()),
        )
        self._store.commit()

    def open_mrs(self) -> int:
        row = self._store.execute(
            "SELECT count(*) AS c FROM agent_mrs WHERE state='opened'"
        ).fetchone()
        return int(row["c"])

    def replace_mrs(self, project: str, mrs: list[tuple[int, str]]) -> None:
        self._store.execute("DELETE FROM agent_mrs WHERE project=?", (project,))
        now = self._store.clock()
        self._store.executemany(
            "INSERT OR REPLACE INTO agent_mrs (project, iid, state, created) VALUES (?, ?, ?, ?)",
            [(project, iid, st, now) for iid, st in mrs],
        )
        self._store.commit()
