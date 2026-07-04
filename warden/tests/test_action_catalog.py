"""Tests for the action catalog (§09 step 01): Action-ID -> recognizer/
transport mapping, the Built-in-Default, and the `type`-dependent vocabulary.

See docs/design/architecture-generalization/09-endpoint-actions/01-action-catalog.md
for the step this belongs to and 09-endpoint-actions.md §1.2/§5 for the Was/Warum.
"""

from __future__ import annotations

import pytest

from warden.guards.git import actions as git_actions
from warden.guards.gitlab_api import actions as gitlab_actions
from warden.guards.gitlab_api.catalog.write_endpoints import DEFAULT_ENABLED, WRITE_ENDPOINTS


def test_action_to_recognizers_covers_every_write_endpoint_exactly_once() -> None:
    known_ids = {ep.id for ep in WRITE_ENDPOINTS}
    covered_by: dict[str, str] = {}
    for action, recognizer_ids in gitlab_actions.ACTION_TO_RECOGNIZERS.items():
        for recognizer_id in recognizer_ids:
            assert recognizer_id in known_ids, (
                f"{action!r} references unknown recognizer {recognizer_id!r}"
            )
            assert recognizer_id not in covered_by, (
                f"{recognizer_id!r} double-mapped by {covered_by.get(recognizer_id)!r} "
                f"and {action!r}"
            )
            covered_by[recognizer_id] = action
    assert covered_by.keys() == known_ids


def test_mr_comment_covers_exactly_note_discussion_and_reply() -> None:
    assert gitlab_actions.ACTION_TO_RECOGNIZERS[gitlab_actions.MR_COMMENT] == (
        "mr.note",
        "mr.discussion",
        "mr.discussion_reply",
    )


def test_default_actions_span_exactly_default_enabled_plus_transport() -> None:
    rest_recognizers = {
        recognizer_id
        for action in gitlab_actions.DEFAULT_ACTIONS
        for recognizer_id in gitlab_actions.ACTION_TO_RECOGNIZERS.get(action, ())
    }
    assert rest_recognizers == DEFAULT_ENABLED
    assert git_actions.GIT_FETCH in gitlab_actions.DEFAULT_ACTIONS
    assert git_actions.GIT_PUSH in gitlab_actions.DEFAULT_ACTIONS


def test_actions_valid_for_type_plain() -> None:
    assert gitlab_actions.actions_valid_for_type("plain") == {
        git_actions.GIT_FETCH,
        git_actions.GIT_PUSH,
    }


def test_actions_valid_for_type_gitlab_contains_all_eight() -> None:
    valid = gitlab_actions.actions_valid_for_type("gitlab")
    assert len(valid) == 8
    assert valid == gitlab_actions.ALL_ACTIONS
    assert valid == {
        "git.fetch",
        "git.push",
        "mr.create",
        "mr.comment",
        "mr.update",
        "pipeline.trigger",
        "branch.create",
        "issue.create",
    }


def test_actions_valid_for_type_github_not_implemented() -> None:
    with pytest.raises(ValueError):
        gitlab_actions.actions_valid_for_type("github")


def test_actions_valid_for_type_unknown_type() -> None:
    with pytest.raises(ValueError):
        gitlab_actions.actions_valid_for_type("bogus")


@pytest.mark.parametrize(
    "operation,service,expected",
    [
        ("advertise", "git-upload-pack", git_actions.GIT_FETCH),
        ("upload-pack", "git-upload-pack", git_actions.GIT_FETCH),
        ("advertise", "git-receive-pack", git_actions.GIT_PUSH),
        ("receive-pack", "git-receive-pack", git_actions.GIT_PUSH),
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
