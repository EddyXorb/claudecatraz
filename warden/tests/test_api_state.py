"""MrState: the REST-API guard's per-endpoint MR-quota table. An off-by-one
here would directly mis-gate writes.
"""

from __future__ import annotations

from warden.core.state import State
from warden.guards.git.gitlab.state import MrState


def _mr_state() -> MrState:
    return MrState(State(":memory:").store)


def test_open_mrs_counts_only_opened():
    ms = _mr_state()
    ms.upsert_mr("h", "group/proj", 1, "opened")
    ms.upsert_mr("h", "group/proj", 2, "merged")
    assert ms.open_mrs("h") == 1


def test_upsert_mr_state_transition_updates_count():
    ms = _mr_state()
    ms.upsert_mr("h", "p", 1, "opened")
    assert ms.open_mrs("h") == 1
    ms.upsert_mr("h", "p", 1, "closed")  # same iid transitions, not a new row
    assert ms.open_mrs("h") == 0


def test_replace_mrs_sets_open_count():
    ms = _mr_state()
    ms.replace_mrs("h", "p", [(1, "opened"), (2, "opened"), (3, "merged")])
    assert ms.open_mrs("h") == 2


# --- (host, project) state-keying ----------------------------------------------


def test_two_hosts_with_the_same_project_path_and_iid_get_separate_counters():
    # gitlab.com/acme/infra!5 and my-gitlab.de/acme/infra!5 are different MRs
    # that happen to share both project path and iid — without the host in
    # the key the second upsert would overwrite the first's row.
    ms = _mr_state()
    ms.upsert_mr("gitlab.com", "acme/infra", 5, "opened")
    ms.upsert_mr("my-gitlab.de", "acme/infra", 5, "opened")
    assert ms.open_mrs("gitlab.com") == 1
    assert ms.open_mrs("my-gitlab.de") == 1


def test_open_mrs_counts_only_the_given_endpoint():
    # open_mrs(host) is the per-endpoint quota counter — a third, untouched
    # host must never leak into another host's count.
    ms = _mr_state()
    ms.upsert_mr("gitlab.com", "acme/infra", 1, "opened")
    ms.upsert_mr("gitlab.com", "acme/infra", 2, "opened")
    ms.upsert_mr("other.example", "acme/infra", 1, "opened")
    assert ms.open_mrs("gitlab.com") == 2
    assert ms.open_mrs("other.example") == 1
    assert ms.open_mrs("never-touched.example") == 0


def test_replace_mrs_only_clears_the_matching_host():
    ms = _mr_state()
    ms.upsert_mr("gitlab.com", "acme/infra", 5, "opened")
    ms.upsert_mr("my-gitlab.de", "acme/infra", 5, "opened")
    ms.replace_mrs("gitlab.com", "acme/infra", [])  # reconcile: gitlab.com now empty
    assert ms.open_mrs("gitlab.com") == 0
    assert ms.open_mrs("my-gitlab.de") == 1  # my-gitlab.de's row survives
