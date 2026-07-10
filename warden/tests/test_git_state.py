"""BranchState: the git guard's per-endpoint branch quota table. An off-by-one
here would directly mis-gate pushes.
"""

from __future__ import annotations

from warden.core.state import State
from warden.guards.git.transport.state import BranchState


def _branch_state() -> BranchState:
    return BranchState(State(":memory:").store)


def test_replace_branches_is_scoped_per_project():
    bs = _branch_state()
    bs.replace_branches("h", "a", ["claude/1", "claude/2"])
    bs.replace_branches("h", "b", ["claude/3"])
    assert bs.open_branches("h") == 3
    bs.replace_branches("h", "a", [])  # reconcile finds project A now empty
    assert bs.open_branches("h") == 1  # project B untouched


def test_add_branch_records_a_single_push_created_branch():
    bs = _branch_state()
    bs.add_branch("h", "a", "claude/feature")
    assert bs.open_branches("h") == 1
    bs.add_branch("h", "a", "claude/feature")  # idempotent re-push of the same ref
    assert bs.open_branches("h") == 1


# --- (host, project) state-keying ----------------------------------------------


def test_two_hosts_with_the_same_project_path_get_separate_counters():
    # Different repos can share a project path across hosts — without the host
    # in the key, a push on one would overwrite the other's row.
    bs = _branch_state()
    bs.add_branch("gitlab.com", "acme/infra", "claude/a")
    bs.add_branch("my-gitlab.de", "acme/infra", "claude/a")
    bs.add_branch("my-gitlab.de", "acme/infra", "claude/b")
    assert bs.open_branches("gitlab.com") == 1
    assert bs.open_branches("my-gitlab.de") == 2


def test_open_branches_counts_only_the_given_endpoint():
    # open_branches(host) is the per-endpoint quota counter — a third,
    # untouched host must never leak into another host's count.
    bs = _branch_state()
    bs.add_branch("gitlab.com", "acme/infra", "claude/a")
    bs.add_branch("gitlab.com", "acme/infra", "claude/b")
    bs.add_branch("gitlab.com", "acme/infra", "claude/c")
    bs.add_branch("other.example", "acme/infra", "claude/a")
    assert bs.open_branches("gitlab.com") == 3
    assert bs.open_branches("other.example") == 1
    assert bs.open_branches("never-touched.example") == 0


def test_replace_branches_only_clears_the_matching_host():
    bs = _branch_state()
    bs.add_branch("gitlab.com", "acme/infra", "claude/a")
    bs.add_branch("my-gitlab.de", "acme/infra", "claude/a")
    bs.replace_branches("gitlab.com", "acme/infra", [])  # reconcile: gitlab.com now empty
    assert bs.open_branches("gitlab.com") == 0
    assert bs.open_branches("my-gitlab.de") == 1  # my-gitlab.de's row survives
