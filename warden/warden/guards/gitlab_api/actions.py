"""_BRIDGE_10_03: recognizer id -> new-vocabulary action id.

The git-namespace vocabulary (``warden.guards.git.actions``) now owns the
action ids; this module only bridges the REST write catalog's existing
recognizer ids onto them so ``build_effective_table`` can activate rows from
a host's new-vocabulary ``effective_actions``. 03-gitlab-guard deletes this
module once the REST guard is rewritten onto recognizers directly.
"""

from __future__ import annotations

from typing import Mapping

from ..git import actions as git_actions
from .catalog.write_endpoints import WRITE_ENDPOINTS

_BRIDGE_10_03_RECOGNIZER_TO_ACTION: Mapping[str, str] = {
    "mr.create": git_actions.PROJECT_MR_CREATE.id,
    "mr.note": git_actions.PROJECT_MR_COMMENT.id,
    "mr.discussion": git_actions.PROJECT_MR_COMMENT.id,
    "mr.discussion_reply": git_actions.PROJECT_MR_COMMENT.id,
    "mr.update": git_actions.PROJECT_MR_EDIT.id,
    "pipeline.trigger": git_actions.PROJECT_CI_TRIGGER.id,
    "branch.create": git_actions.REPO_BRANCH_CREATE.id,
    "issue.create": git_actions.PROJECT_ISSUE_CREATE.id,
}


def _validate_bridge_consistency(mapping: Mapping[str, str]) -> None:
    known_recognizer_ids = {ep.id for ep in WRITE_ENDPOINTS}
    unknown_recognizers = sorted(set(mapping) - known_recognizer_ids)
    if unknown_recognizers:
        raise AssertionError(
            f"_BRIDGE_10_03_RECOGNIZER_TO_ACTION references unknown recognizer(s) "
            f"{unknown_recognizers!r}"
        )
    orphaned = sorted(known_recognizer_ids - mapping.keys())
    if orphaned:
        raise AssertionError(
            f"_BRIDGE_10_03_RECOGNIZER_TO_ACTION does not cover WRITE_ENDPOINTS id(s) {orphaned!r}"
        )
    unknown_actions = sorted(set(mapping.values()) - set(git_actions.by_id))
    if unknown_actions:
        raise AssertionError(
            f"_BRIDGE_10_03_RECOGNIZER_TO_ACTION maps to unknown action id(s) {unknown_actions!r}"
        )


_validate_bridge_consistency(_BRIDGE_10_03_RECOGNIZER_TO_ACTION)
