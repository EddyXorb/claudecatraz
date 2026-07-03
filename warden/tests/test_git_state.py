"""BranchState (§E, W8, §6.11, §07 Punkt 6 step 4): the git guard's own branch
quota table (``agent_branches``), built on the same
:class:`~warden.core.state.StateStore` core state uses — split out of
:mod:`test_forge_state` when branch tracking moved from the shared forge to
the git guard itself.

This counter is what the git guard's R5 quota reads via
:meth:`~warden.guards.git.guard.GitGuard.state_view`, so an off-by-one here
would directly mis-gate pushes.
"""

from __future__ import annotations

from warden.core.state import State
from warden.guards.git.state import BranchState


def _branch_state() -> BranchState:
    return BranchState(State(":memory:").store)


def test_replace_branches_is_scoped_per_project():
    bs = _branch_state()
    bs.replace_branches("a", ["claude/1", "claude/2"])
    bs.replace_branches("b", ["claude/3"])
    assert bs.open_branches() == 3
    bs.replace_branches("a", [])  # reconcile finds project A now empty
    assert bs.open_branches() == 1  # project B untouched


def test_add_branch_records_a_single_push_created_branch():
    bs = _branch_state()
    bs.add_branch("a", "claude/feature")
    assert bs.open_branches() == 1
    bs.add_branch("a", "claude/feature")  # idempotent re-push of the same ref
    assert bs.open_branches() == 1
