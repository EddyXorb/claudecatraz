"""_BRIDGE_10_03: the recognizer-id -> new-vocabulary-action-id bridge, plus
the git transport guard's coarse operation -> action mapping.
"""

from __future__ import annotations

import pytest

from warden.guards.git import actions as git_actions
from warden.guards.gitlab_api import actions as gitlab_actions
from warden.guards.gitlab_api.catalog.write_endpoints import WRITE_ENDPOINTS


def test_bridge_covers_every_write_endpoint_exactly_once() -> None:
    known_ids = {ep.id for ep in WRITE_ENDPOINTS}
    bridge = gitlab_actions._BRIDGE_10_03_RECOGNIZER_TO_ACTION
    assert set(bridge) == known_ids
    for action_id in bridge.values():
        assert action_id in git_actions.by_id


def test_mr_comment_recognizers_all_bridge_to_project_mr_comment() -> None:
    bridge = gitlab_actions._BRIDGE_10_03_RECOGNIZER_TO_ACTION
    assert bridge["mr.note"] == git_actions.PROJECT_MR_COMMENT.id
    assert bridge["mr.discussion"] == git_actions.PROJECT_MR_COMMENT.id
    assert bridge["mr.discussion_reply"] == git_actions.PROJECT_MR_COMMENT.id


@pytest.mark.parametrize(
    "operation,service,expected",
    [
        ("advertise", "git-upload-pack", git_actions.REPO_READ.id),
        ("upload-pack", "git-upload-pack", git_actions.REPO_READ.id),
        ("advertise", "git-receive-pack", git_actions.REPO_BRANCH_PUSH.id),
        ("receive-pack", "git-receive-pack", git_actions.REPO_BRANCH_PUSH.id),
    ],
)
def test_action_for_git_operation(operation: str, service: str, expected: str) -> None:
    assert git_actions.action_for_git_operation(operation, service) == expected


def test_action_for_git_operation_rejects_unknown_operation() -> None:
    with pytest.raises(ValueError):
        git_actions.action_for_git_operation("bogus")


def test_action_for_git_operation_rejects_unknown_service_on_advertise() -> None:
    with pytest.raises(ValueError):
        git_actions.action_for_git_operation("advertise", "bogus-service")
