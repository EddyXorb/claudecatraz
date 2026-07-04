"""BranchState (§E, W8, §6.11, §07 Punkt 6 step 4, §07 Punkt 8 follow-up): the
git guard's own branch quota table (``agent_branches``), built on the same
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
    bs.replace_branches("h", "a", ["claude/1", "claude/2"])
    bs.replace_branches("h", "b", ["claude/3"])
    assert bs.open_branches() == 3
    bs.replace_branches("h", "a", [])  # reconcile finds project A now empty
    assert bs.open_branches() == 1  # project B untouched


def test_add_branch_records_a_single_push_created_branch():
    bs = _branch_state()
    bs.add_branch("h", "a", "claude/feature")
    assert bs.open_branches() == 1
    bs.add_branch("h", "a", "claude/feature")  # idempotent re-push of the same ref
    assert bs.open_branches() == 1


# --- (host, project) state-keying (§07 Punkt 8 follow-up, design spike section 4) --


def test_two_hosts_with_the_same_project_path_get_separate_counters():
    # gitlab.com/acme/infra and my-gitlab.de/acme/infra are different repos
    # that happen to share a project path — without the host in the key a
    # push on one would silently share/overwrite the other's row.
    bs = _branch_state()
    bs.add_branch("gitlab.com", "acme/infra", "claude/a")
    bs.add_branch("my-gitlab.de", "acme/infra", "claude/a")
    bs.add_branch("my-gitlab.de", "acme/infra", "claude/b")
    assert bs.open_branches() == 3  # all three rows counted, none overwrote another


def test_replace_branches_only_clears_the_matching_host():
    bs = _branch_state()
    bs.add_branch("gitlab.com", "acme/infra", "claude/a")
    bs.add_branch("my-gitlab.de", "acme/infra", "claude/a")
    bs.replace_branches("gitlab.com", "acme/infra", [])  # reconcile: gitlab.com now empty
    assert bs.open_branches() == 1  # my-gitlab.de's row survives
