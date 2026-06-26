"""state.py (W8, §6.11): quota counting, the rolling write-window, and the
fail-safe lock that holds until the first reconcile.

These counters are what the policy's R5 quotas read, so an off-by-one or a
missed time-window here would directly mis-gate writes.
"""

from __future__ import annotations

import sqlite3

import pytest

from warden.state import WINDOW_SECONDS, State


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
