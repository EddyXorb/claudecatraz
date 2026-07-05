"""The git guard's own quota table: ``agent_branches``, living in the same
SQLite file as ``warden.core.state`` via the shared ``StateStore`` — never a
second connection.

The git guard tracks its own branch quota, independent of the GitLab
REST-API guard's MR tracking.

Keyed by ``(host, project, ref)``: two different hosts can coincidentally
share a project path (``gitlab.com/acme/infra`` vs. ``my-gitlab.de/acme/infra``);
without the host in the key a push on one would silently share/overwrite the
other's row. ``open_branches`` is per-endpoint: ``max_open_branches`` is
overridable per ``[[git.endpoint]]`` (``Config.effective_rules``), so the
counter it is checked against must be scoped to that same endpoint — a
global count would let one endpoint's pushes exhaust every other endpoint's
quota. A deployment with a single ``[[git.endpoint]]`` simply always queries
the one host every row carries, so this table is behaviourally identical to
a global count for that case.
"""

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
