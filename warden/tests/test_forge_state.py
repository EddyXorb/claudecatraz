"""ForgeState (§E, W8, §6.11): the forge domain's own quota tables
(``agent_branches``/``agent_mrs``), built on the same :class:`~warden.core.state.StateStore`
core state uses — split out of :mod:`test_state` when the branch/MR tables
moved out of core.

These counters are what the git/REST guards' R5 quotas read via
:meth:`~warden.guards.gitlab.forge.GitForge.state_view`, so an off-by-one
here would directly mis-gate writes.
"""

from __future__ import annotations

from warden.core.state import State
from warden.guards.gitlab.state import ForgeState


def _forge_state() -> ForgeState:
    return ForgeState(State(":memory:").store)


def test_open_mrs_counts_only_opened():
    fs = _forge_state()
    fs.upsert_mr("group/proj", 1, "opened")
    fs.upsert_mr("group/proj", 2, "merged")
    assert fs.open_mrs() == 1


def test_upsert_mr_state_transition_updates_count():
    fs = _forge_state()
    fs.upsert_mr("p", 1, "opened")
    assert fs.open_mrs() == 1
    fs.upsert_mr("p", 1, "closed")  # same iid transitions, not a new row
    assert fs.open_mrs() == 0


def test_replace_branches_is_scoped_per_project():
    fs = _forge_state()
    fs.replace_branches("a", ["claude/1", "claude/2"])
    fs.replace_branches("b", ["claude/3"])
    assert fs.open_branches() == 3
    fs.replace_branches("a", [])  # reconcile finds project A now empty
    assert fs.open_branches() == 1  # project B untouched


def test_replace_mrs_sets_open_count():
    fs = _forge_state()
    fs.replace_mrs("p", [(1, "opened"), (2, "opened"), (3, "merged")])
    assert fs.open_mrs() == 2
