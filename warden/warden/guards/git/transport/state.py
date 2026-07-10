"""The git guard's own quota table: agent_branches, in the same SQLite
file as core.state, never a second connection.

Keyed by (host, project, ref) so two hosts sharing a project path never
collide. open_branches is per-endpoint, scoped to the same host."""

from __future__ import annotations

from ....core.state import StateStore

_BRANCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_branches (
  host TEXT NOT NULL DEFAULT '', project TEXT, ref TEXT, created REAL,
  PRIMARY KEY (host, project, ref)
);
"""


class BranchState:
    def __init__(self, store: StateStore) -> None:
        self._store = store
        self._store.executescript(_BRANCH_SCHEMA)

    def add_branch(self, host: str, project: str, ref: str) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO agent_branches (host, project, ref, created) "
            "VALUES (?, ?, ?, ?)",
            (host, project, ref, self._store.clock()),
        )
        self._store.commit()

    def open_branches(self, host: str) -> int:
        row = self._store.execute(
            "SELECT count(*) AS c FROM agent_branches WHERE host=?", (host,)
        ).fetchone()
        return int(row["c"])

    def replace_branches(self, host: str, project: str, refs: list[str]) -> None:
        self._store.execute(
            "DELETE FROM agent_branches WHERE host=? AND project=?", (host, project)
        )
        now = self._store.clock()
        self._store.executemany(
            "INSERT OR REPLACE INTO agent_branches (host, project, ref, created) "
            "VALUES (?, ?, ?, ?)",
            [(host, project, r, now) for r in refs],
        )
        self._store.commit()
