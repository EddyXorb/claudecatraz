"""The REST-API guard's own quota table: ``agent_mrs``, living in the same
SQLite file as :mod:`warden.core.state` via the shared
:class:`~warden.core.state.StateStore` — never a second connection.

Folded here from the now-dissolved ``guards.gitlab.state.ForgeState``
(§07 Punkt 6, step 5): branch tracking lives in the git guard's own
:mod:`warden.guards.git.state`; this table is the REST-API guard's MR-quota
domain only.

Keyed by ``(host, project, iid)`` (§07 Punkt 8 follow-up, design spike
section 4) for the same reason :mod:`warden.guards.git.state` is: two
different hosts can coincidentally share a project path, and without the
host in the key their MR ids would collide. :meth:`open_mrs` stays a
**global**, unfiltered count — see that module's docstring for why. In
single-target mode every row's ``host`` is the same constant value
(``Config.implicit_host``), so this table behaves identically to the
pre-host-column schema.
"""

from __future__ import annotations

from ...core.state import StateStore

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

    def open_mrs(self) -> int:
        row = self._store.execute(
            "SELECT count(*) AS c FROM agent_mrs WHERE state='opened'"
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
