"""The transport guard's ``SUPPORTED`` ceiling: the repo-scoped git actions."""

from __future__ import annotations

from warden.guards.git import actions as git_actions
from warden.guards.git.transport import actions as transport_actions


def test_supported_is_exactly_the_repo_scope():
    assert transport_actions.SUPPORTED == {
        git_actions.REPO_READ,
        git_actions.REPO_BRANCH_CREATE,
        git_actions.REPO_BRANCH_PUSH,
        git_actions.REPO_BRANCH_DELETE,
        git_actions.REPO_TAG_CREATE,
        git_actions.REPO_TAG_DELETE,
    }


def test_supported_is_a_subset_of_the_namespace_vocabulary():
    assert transport_actions.SUPPORTED <= git_actions.ALL
