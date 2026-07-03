"""The forge domain's own quota tables (§E): ``agent_branches``/``agent_mrs``,
living in the same SQLite file as :mod:`warden.core.state` via the shared
:class:`~warden.core.state.StateStore` — never a second connection.

Named for what they track (the agent's own namespace-scoped branches/MRs,
§03.5), not for a specific guard; both the git guard and the REST-API guard
read this through :class:`~warden.guards.gitlab.forge.GitlabForge`.
"""

from __future__ import annotations

from ...core.state import StateStore

_FORGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_branches (
  project TEXT, ref TEXT, created REAL,
  PRIMARY KEY (project, ref)
);
CREATE TABLE IF NOT EXISTS agent_mrs (
  project TEXT, iid INTEGER, state TEXT, created REAL,
  PRIMARY KEY (project, iid)
);
"""


class ForgeState:
    def __init__(self, store: StateStore) -> None:
        self._store = store
        self._store.executescript(_FORGE_SCHEMA)

    def add_branch(self, project: str, ref: str) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO agent_branches (project, ref, created) VALUES (?, ?, ?)",
            (project, ref, self._store.clock()),
        )
        self._store.commit()

    def upsert_mr(self, project: str, iid: int, state: str) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO agent_mrs (project, iid, state, created) VALUES "
            "(?, ?, ?, COALESCE((SELECT created FROM agent_mrs WHERE project=? AND iid=?), ?))",
            (project, iid, state, project, iid, self._store.clock()),
        )
        self._store.commit()

    def open_branches(self) -> int:
        row = self._store.execute("SELECT count(*) AS c FROM agent_branches").fetchone()
        return int(row["c"])

    def open_mrs(self) -> int:
        row = self._store.execute(
            "SELECT count(*) AS c FROM agent_mrs WHERE state='opened'"
        ).fetchone()
        return int(row["c"])

    def replace_branches(self, project: str, refs: list[str]) -> None:
        self._store.execute("DELETE FROM agent_branches WHERE project=?", (project,))
        now = self._store.clock()
        self._store.executemany(
            "INSERT OR REPLACE INTO agent_branches (project, ref, created) VALUES (?, ?, ?)",
            [(project, r, now) for r in refs],
        )
        self._store.commit()

    def replace_mrs(self, project: str, mrs: list[tuple[int, str]]) -> None:
        self._store.execute("DELETE FROM agent_mrs WHERE project=?", (project,))
        now = self._store.clock()
        self._store.executemany(
            "INSERT OR REPLACE INTO agent_mrs (project, iid, state, created) VALUES (?, ?, ?, ?)",
            [(project, iid, st, now) for iid, st in mrs],
        )
        self._store.commit()
