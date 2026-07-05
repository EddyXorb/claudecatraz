"""state.py (W8, §6.11): quota counting, the rolling write-window, and the
fail-safe lock that holds until the first reconcile — the core-only state.
Branch counter tests live in :mod:`test_git_state` (they exercise
:class:`~warden.guards.git.state.BranchState`); MR counter tests live in
:mod:`test_api_state` (:class:`~warden.guards.gitlab_api.state.MrState`) —
neither is core.

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
from warden.guards.git.transport.state import BranchState
from warden.guards.gitlab_api.state import MrState


def _clocked(start=1000.0):
    now = {"t": start}
    return State(":memory:", clock=lambda: now["t"]), now


# --- fail-safe lock ------------------------------------------------------------
def test_view_is_locked_until_reconciled():
    st = State(":memory:")
    assert st.view("git", "h").locked is True  # never "empty = all free"
    st.mark_reconciled("git")
    assert st.view("git", "h").locked is False


# --- rolling write window ------------------------------------------------------
def test_writes_last_hour_drops_records_past_the_window():
    st, now = _clocked()
    st.mark_reconciled("git")
    st.record_write("api", "h", "mr", "1")
    assert st.writes_last_hour("h") == 1
    now["t"] += WINDOW_SECONDS + 1  # roll past the hour
    assert st.writes_last_hour("h") == 0


def test_writes_last_hour_is_scoped_per_host():
    # step 04: max_writes_per_hour is a per-endpoint quota, so the counter it
    # is checked against must not let one host's writes count against another's.
    st, _ = _clocked()
    st.mark_reconciled("api")
    st.record_write("api", "gitlab.com", "mr", "1")
    st.record_write("api", "gitlab.com", "mr", "2")
    st.record_write("api", "my-gitlab.de", "mr", "1")
    assert st.writes_last_hour("gitlab.com") == 2
    assert st.writes_last_hour("my-gitlab.de") == 1


def test_prune_physically_deletes_aged_rows():
    st, now = _clocked()
    st.record_write("api", "h", "mr", "old")
    now["t"] += WINDOW_SECONDS + 1
    st.record_write("api", "h", "mr", "fresh")
    st.prune()
    # white-box: prune bounds table growth, so the aged row is gone from disk,
    # not just filtered out of the count.
    remaining = st.store.execute("SELECT count(*) AS c FROM writes").fetchone()["c"]
    assert remaining == 1


def test_close_releases_the_connection():
    st = State(":memory:")
    st.close()
    with pytest.raises(sqlite3.ProgrammingError):
        st.record_write("api", "h", "mr", "x")  # connection is gone after close()


def test_view_reflects_writes_counter_once_reconciled():
    st, _ = _clocked()
    st.mark_reconciled("git")
    st.record_write("git", "h", "push", "claude/a")
    v = st.view("git", "h")
    # open_branches/open_mrs default to 0 on the core-only view — each guard
    # fills its own via its own state_view (test_git_reconcile.py/test_api_reconcile.py).
    assert (v.open_branches, v.open_mrs, v.writes_last_hour, v.locked) == (0, 0, 1, False)


# --- schema versioning (no migrations, pre-1.0: version-stamp + fail-closed) --


def test_fresh_db_is_created_at_current_schema_version():
    st = State(":memory:")
    assert st.schema_version() == CURRENT_SCHEMA_VERSION


def test_fresh_db_has_target_tables():
    """A brand-new DB gets the current shape directly — no legacy names, no
    lift needed: ``agent_branches`` (the git guard's own :class:`BranchState`),
    ``agent_mrs`` (the REST-API guard's own :class:`MrState`, both sharing the
    same connection) and ``writes.guard``."""
    st = State(":memory:")
    bs = BranchState(st.store)
    ms = MrState(st.store)
    assert bs.open_branches("h") == 0
    assert ms.open_mrs("h") == 0
    st.record_write("git", "h", "push", "claude/a")
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


def test_older_schema_version_also_fails_closed(tmp_path):
    """§07 Punkt 8 follow-up: a non-fresh DB stamped at an *older* version than
    this build (e.g. v1, pre-``host``-column) must also abort, not be silently
    re-stamped and reused — ``CREATE TABLE IF NOT EXISTS`` cannot retrofit the
    new column onto the old-shaped ``agent_branches``/``agent_mrs`` tables.
    Only a brand-new file (``user_version == 0``) is exempt (the bootstrap
    case, not a mismatch)."""
    assert CURRENT_SCHEMA_VERSION > 1, "test assumes an older stamped version exists"
    path = tmp_path / "stale.db"
    raw = sqlite3.connect(str(path))
    raw.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION - 1}")
    raw.commit()
    raw.close()

    with pytest.raises(SchemaError):
        State(str(path))


def test_reopening_a_current_db_is_idempotent(tmp_path):
    path = tmp_path / "state.db"
    st1 = State(str(path))
    st1.mark_reconciled("git")
    st1.close()

    st2 = State(str(path))
    assert st2.schema_version() == CURRENT_SCHEMA_VERSION
    assert st2.is_reconciled("git") is True  # data from the first open survived reopening
    st2.close()
