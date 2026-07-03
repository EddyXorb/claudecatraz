"""The git guard's own quota table: ``agent_branches``, living in the same
SQLite file as :mod:`warden.core.state` via the shared
:class:`~warden.core.state.StateStore` — never a second connection.

git-owned (§07 Punkt 6, step 4): the git guard tracks its own branch quota,
independent of the GitLab REST-API guard's MR tracking.
"""

from __future__ import annotations

from ...core.state import StateStore

_BRANCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_branches (
  project TEXT, ref TEXT, created REAL,
  PRIMARY KEY (project, ref)
);
"""


class BranchState:
    def __init__(self, store: StateStore) -> None:
        self._store = store
        self._store.executescript(_BRANCH_SCHEMA)

    def add_branch(self, project: str, ref: str) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO agent_branches (project, ref, created) VALUES (?, ?, ?)",
            (project, ref, self._store.clock()),
        )
        self._store.commit()

    def open_branches(self) -> int:
        row = self._store.execute("SELECT count(*) AS c FROM agent_branches").fetchone()
        return int(row["c"])

    def replace_branches(self, project: str, refs: list[str]) -> None:
        self._store.execute("DELETE FROM agent_branches WHERE project=?", (project,))
        now = self._store.clock()
        self._store.executemany(
            "INSERT OR REPLACE INTO agent_branches (project, ref, created) VALUES (?, ?, ?)",
            [(project, r, now) for r in refs],
        )
        self._store.commit()
