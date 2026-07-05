"""_BRIDGE_10_03: the recognizer-id -> new-vocabulary-action-id bridge."""

from __future__ import annotations

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
