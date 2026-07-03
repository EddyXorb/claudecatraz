"""state.py (W8, §6.11): quota counting, the rolling write-window, and the
fail-safe lock that holds until the first reconcile.

These counters are what the policy's R5 quotas read, so an off-by-one or a
missed time-window here would directly mis-gate writes.
"""

from __future__ import annotations

import sqlite3

import pytest

from warden.core.state import (
    BASE_SCHEMA_VERSION,
    CURRENT_SCHEMA_VERSION,
    WINDOW_SECONDS,
    SchemaError,
    State,
)


def _clocked(start=1000.0):
    now = {"t": start}
    return State(":memory:", clock=lambda: now["t"]), now


# --- fail-safe lock ------------------------------------------------------------
def test_view_is_locked_until_reconciled():
    st = State(":memory:")
    assert st.view().locked is True  # never "empty = all free"
    st.mark_reconciled()
    assert st.view().locked is False


# --- rolling write window ------------------------------------------------------
def test_writes_last_hour_drops_records_past_the_window():
    st, now = _clocked()
    st.mark_reconciled()
    st.record_write("api", "mr", "1")
    assert st.writes_last_hour() == 1
    now["t"] += WINDOW_SECONDS + 1  # roll past the hour
    assert st.writes_last_hour() == 0


def test_prune_physically_deletes_aged_rows():
    st, now = _clocked()
    st.record_write("api", "mr", "old")
    now["t"] += WINDOW_SECONDS + 1
    st.record_write("api", "mr", "fresh")
    st.prune()
    # white-box: prune bounds table growth, so the aged row is gone from disk,
    # not just filtered out of the count.
    remaining = st._db.execute("SELECT count(*) AS c FROM writes").fetchone()["c"]
    assert remaining == 1


# --- branch / MR counters ------------------------------------------------------
def test_open_mrs_counts_only_opened():
    st = State(":memory:")
    st.mark_reconciled()
    st.upsert_mr("group/proj", 1, "opened")
    st.upsert_mr("group/proj", 2, "merged")
    assert st.open_mrs() == 1


def test_upsert_mr_state_transition_updates_count():
    st = State(":memory:")
    st.mark_reconciled()
    st.upsert_mr("p", 1, "opened")
    assert st.open_mrs() == 1
    st.upsert_mr("p", 1, "closed")  # same iid transitions, not a new row
    assert st.open_mrs() == 0


def test_replace_branches_is_scoped_per_project():
    st = State(":memory:")
    st.mark_reconciled()
    st.replace_branches("a", ["claude/1", "claude/2"])
    st.replace_branches("b", ["claude/3"])
    assert st.open_branches() == 3
    st.replace_branches("a", [])  # reconcile finds project A now empty
    assert st.open_branches() == 1  # project B untouched


def test_replace_mrs_sets_open_count():
    st = State(":memory:")
    st.mark_reconciled()
    st.replace_mrs("p", [(1, "opened"), (2, "opened"), (3, "merged")])
    assert st.open_mrs() == 2


def test_close_releases_the_connection():
    st = State(":memory:")
    st.close()
    with pytest.raises(sqlite3.ProgrammingError):
        st.record_write("api", "mr", "x")  # connection is gone after close()


def test_view_reflects_all_counters_once_reconciled():
    st, _ = _clocked()
    st.mark_reconciled()
    st.replace_branches("p", ["claude/a"])
    st.replace_mrs("p", [(1, "opened")])
    st.record_write("git", "push", "claude/a")
    v = st.view()
    assert (v.open_branches, v.open_mrs, v.writes_last_hour, v.locked) == (1, 1, 1, False)


# --- schema versioning (§06-migration.md Schritt 2, F11 precondition) ----------


def test_fresh_db_is_created_at_current_schema_version():
    st = State(":memory:")
    assert st.schema_version() == CURRENT_SCHEMA_VERSION


def test_migrations_span_exactly_base_to_current():
    from warden.core.state import MIGRATIONS

    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(versions), "migrations must be listed in order"
    assert versions[0] == BASE_SCHEMA_VERSION + 1
    assert versions[-1] == CURRENT_SCHEMA_VERSION


def test_legacy_unversioned_db_is_migrated_without_data_loss(tmp_path):
    """A pre-existing DB (the old, unversioned shape: tables present, no
    ``user_version`` marker) is lifted to :data:`CURRENT_SCHEMA_VERSION` in
    place — no table recreated from scratch, no row dropped."""
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(
        """
        CREATE TABLE writes (
          id INTEGER PRIMARY KEY, ts REAL NOT NULL,
          channel TEXT NOT NULL, kind TEXT NOT NULL, ref_or_iid TEXT
        );
        CREATE TABLE claude_branches (project TEXT, ref TEXT, created REAL,
                                       PRIMARY KEY (project, ref));
        CREATE TABLE claude_mrs (project TEXT, iid INTEGER, state TEXT, created REAL,
                                  PRIMARY KEY (project, iid));
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    raw.execute(
        "INSERT INTO claude_branches (project, ref, created) VALUES (?, ?, ?)",
        ("group/proj", "claude/legacy", 1000.0),
    )
    raw.execute("INSERT INTO meta (key, value) VALUES ('last_reconcile', '1000.0')")
    raw.commit()
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 0  # sanity: truly unversioned
    raw.close()

    st = State(str(path))
    assert st.schema_version() == CURRENT_SCHEMA_VERSION
    assert st.open_branches() == 1  # the pre-existing row survived the lift
    assert st.is_reconciled() is True  # so did the reconcile marker
    st.close()


def test_future_schema_version_fails_closed(tmp_path):
    """An unrecognised (too new) schema version must abort, never run anyway (A9)."""
    path = tmp_path / "future.db"
    raw = sqlite3.connect(str(path))
    raw.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")
    raw.commit()
    raw.close()

    with pytest.raises(SchemaError):
        State(str(path))


def test_reopening_a_current_db_is_idempotent(tmp_path):
    path = tmp_path / "state.db"
    st1 = State(str(path))
    st1.mark_reconciled()
    st1.close()

    st2 = State(str(path))
    assert st2.schema_version() == CURRENT_SCHEMA_VERSION
    assert st2.is_reconciled() is True  # data from the first open survived reopening
    st2.close()
