"""state.py (W8, §6.11): quota counting, the rolling write-window, and the
fail-safe lock that holds until the first reconcile — the core-only state.
Branch/MR counter tests live in :mod:`test_forge_state` (they exercise
:class:`~warden.guards.gitlab.state.ForgeState`, not core).

These counters are what the policy's R5 quotas read, so an off-by-one or a
missed time-window here would directly mis-gate writes.
"""

from __future__ import annotations

import sqlite3

import pytest

from warden.core.state import (
    CURRENT_SCHEMA_VERSION,
    WINDOW_SECONDS,
    SchemaError,
    State,
)
from warden.guards.gitlab.state import ForgeState


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
    remaining = st.store.execute("SELECT count(*) AS c FROM writes").fetchone()["c"]
    assert remaining == 1


def test_close_releases_the_connection():
    st = State(":memory:")
    st.close()
    with pytest.raises(sqlite3.ProgrammingError):
        st.record_write("api", "mr", "x")  # connection is gone after close()


def test_view_reflects_writes_counter_once_reconciled():
    st, _ = _clocked()
    st.mark_reconciled()
    st.record_write("git", "push", "claude/a")
    v = st.view()
    # open_branches/open_mrs default to 0 on the core-only view — a domain
    # (the forge) fills those via its own combined state_view (test_forge.py).
    assert (v.open_branches, v.open_mrs, v.writes_last_hour, v.locked) == (0, 0, 1, False)


# --- schema versioning (no migrations, pre-1.0: version-stamp + fail-closed) --


def test_fresh_db_is_created_at_current_schema_version():
    st = State(":memory:")
    assert st.schema_version() == CURRENT_SCHEMA_VERSION


def test_fresh_db_has_target_tables():
    """A brand-new DB gets the current shape directly — no legacy names, no
    lift needed: ``agent_branches``/``agent_mrs`` (via ForgeState, sharing the
    same connection) and ``writes.guard``."""
    st = State(":memory:")
    fs = ForgeState(st.store)
    assert fs.open_branches() == 0
    assert fs.open_mrs() == 0
    st.record_write("git", "push", "claude/a")
    row = st.store.execute("SELECT guard, kind FROM writes").fetchone()
    assert (row["guard"], row["kind"]) == ("git", "push")
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
