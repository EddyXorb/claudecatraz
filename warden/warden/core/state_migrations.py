"""State-DB schema migrations: versioned lifts from one schema shape to the next.

Split out of :mod:`warden.core.state` — this module owns *only* the versioned
lift from one schema shape to the next; ``state.py`` keeps the runtime API
(``State``) that reads/writes the current shape.

**Version history** (also documented in ``core/audit.py`` for the audit-log's
own, independent version counter — the two are unrelated schemas with
unrelated numbers):

* **1** — the historical, implicit shape: ``claude_branches``/``claude_mrs``,
  a ``writes.channel`` column, no ``user_version`` marker at all (predates
  this module).
* **2** — introduces the version marker itself via SQLite's ``PRAGMA user_version``.
  No table change.
* **3** — ``claude_branches``/``claude_mrs`` → ``agent_branches``/``agent_mrs``,
  ``writes.channel`` → ``writes.guard``: the claude→agent, channel→guard
  vocabulary shift ("claude" stays only the default namespace-prefix value,
  never a code identifier). Both renames are lossless (``ALTER TABLE ... RENAME TO`` /
  ``RENAME COLUMN``, SQLite ≥3.25).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, Final


class SchemaError(RuntimeError):
    """Raised when the state DB's schema version is newer than this build
    understands — fail-closed: a downgrade must never silently run against a
    shape it does not fully know, so it refuses to start."""


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
    """Version 1 → 2: no table changes — see module docstring for details."""
    # Intentionally empty — the runner stamps PRAGMA user_version regardless.


def _rename_agent_tables(conn: sqlite3.Connection) -> None:
    """Version 2 → 3: claude→agent, channel→guard. Both renames land together
    as one migration (one audit-visible vocabulary change, not two independent
    ones). Reached from either a v1 DB (already carrying these tables/columns)
    or a v2 DB (stamped but never touched table names).
    """
    conn.execute("ALTER TABLE claude_branches RENAME TO agent_branches")
    conn.execute("ALTER TABLE claude_mrs RENAME TO agent_mrs")
    conn.execute("ALTER TABLE writes RENAME COLUMN channel TO guard")


# Legacy DBs (pre-dating this module) are implicitly version 1: they already
# have ``claude_branches``/``claude_mrs`` but no ``user_version`` marker.
BASE_SCHEMA_VERSION: Final[int] = 1

MIGRATIONS: tuple[Migration, ...] = (
    Migration(2, "stamp_schema_version", _stamp_schema_version),
    Migration(3, "rename_agent_tables", _rename_agent_tables),
)

CURRENT_SCHEMA_VERSION: Final[int] = MIGRATIONS[-1].version if MIGRATIONS else BASE_SCHEMA_VERSION


def _has_legacy_tables(conn: sqlite3.Connection) -> bool:
    """True iff pre-migration tables already exist (a real legacy DB, not a
    brand-new file) — the signal that distinguishes "nothing to migrate,
    already fresh" from "unversioned, needs lifting to current". Checks the
    v1 table name specifically (not ``agent_branches``): a v1 DB is the only
    shape a fresh, versioned build never produces itself."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='claude_branches'"
    ).fetchone()
    return row is not None


def run_migrations(conn: sqlite3.Connection) -> None:
    """Lift the DB to :data:`CURRENT_SCHEMA_VERSION`, fail-closed on a future one.

    Must run *before* the caller's ``CREATE TABLE IF NOT EXISTS`` schema,
    otherwise a legacy DB and a brand-new one look identical to
    :func:`_has_legacy_tables`. A brand-new file has nothing to lift — it is
    created straight at :data:`CURRENT_SCHEMA_VERSION` (by the caller's schema
    script, already under the current names). A legacy, unversioned file
    (``user_version`` 0 but tables already present) starts at
    :data:`BASE_SCHEMA_VERSION` and runs every migration above it, in order,
    without losing a row.
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
