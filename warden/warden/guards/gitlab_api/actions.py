"""Action catalog, forge side: maps action IDs to the recognizer sets they
cover, plus the built-in default and the per-``type`` vocabulary.

An action is a code-defined, closed group of recognizers, not a 1:1 mapping
to wire calls: enabling ``mr.note`` but forgetting ``mr.discussion_reply``
would leave an agent unable to reply to review threads, so the grouping
happens here rather than in config. It is never finer than a recognizer —
field-dependent distinctions (e.g. ``state_event=merge`` within
``mr.update``) belong to the capability layer, not this catalog.

:func:`_validate_consistency` runs at import time: every recognizer in
:data:`~.catalog.write_endpoints.WRITE_ENDPOINTS` must be covered by exactly
one action. A violation means the two tables were edited out of step — a
programmer error, hence ``AssertionError`` rather than ``ConfigError``.
"""

from __future__ import annotations

from typing import Mapping

from ..git.actions import GIT_FETCH, GIT_PUSH
from .catalog.write_endpoints import DEFAULT_ENABLED, WRITE_ENDPOINTS

MR_CREATE = "mr.create"
MR_COMMENT = "mr.comment"
MR_UPDATE = "mr.update"
PIPELINE_TRIGGER = "pipeline.trigger"
BRANCH_CREATE = "branch.create"
ISSUE_CREATE = "issue.create"

ACTION_TO_RECOGNIZERS: Mapping[str, tuple[str, ...]] = {
    MR_CREATE: ("mr.create",),
    MR_COMMENT: ("mr.note", "mr.discussion", "mr.discussion_reply"),
    MR_UPDATE: ("mr.update",),
    PIPELINE_TRIGGER: ("pipeline.trigger",),
    BRANCH_CREATE: ("branch.create",),
    ISSUE_CREATE: ("issue.create",),
}


def _validate_consistency(mapping: Mapping[str, tuple[str, ...]]) -> None:
    known_recognizer_ids = {ep.id for ep in WRITE_ENDPOINTS}
    covered_by: dict[str, str] = {}
    for action, recognizer_ids in mapping.items():
        for recognizer_id in recognizer_ids:
            if recognizer_id not in known_recognizer_ids:
                raise AssertionError(
                    f"action catalog: action {action!r} references recognizer "
                    f"{recognizer_id!r}, which is not in WRITE_ENDPOINTS"
                )
            if recognizer_id in covered_by:
                raise AssertionError(
                    f"action catalog: recognizer {recognizer_id!r} is mapped by both "
                    f"{covered_by[recognizer_id]!r} and {action!r} — must be exactly one action"
                )
            covered_by[recognizer_id] = action
    orphaned = known_recognizer_ids - covered_by.keys()
    if orphaned:
        raise AssertionError(
            f"action catalog: WRITE_ENDPOINTS id(s) {sorted(orphaned)!r} are not covered "
            "by any action in ACTION_TO_RECOGNIZERS"
        )


_validate_consistency(ACTION_TO_RECOGNIZERS)

# Built-in default: DEFAULT_ENABLED plus the two transport verbs. Applies
# whenever a warden.toml has no `actions` key — an absent key is distinct
# from an explicit empty list.
DEFAULT_ACTIONS: tuple[str, ...] = (
    GIT_FETCH,
    GIT_PUSH,
    MR_CREATE,
    MR_COMMENT,
    MR_UPDATE,
    PIPELINE_TRIGGER,
)

# The full closed vocabulary: git transport verbs + forge (REST) actions.
FORGE_ACTIONS: frozenset[str] = frozenset(ACTION_TO_RECOGNIZERS)
ALL_ACTIONS: frozenset[str] = FORGE_ACTIONS | frozenset({GIT_FETCH, GIT_PUSH})

_PLAIN_ACTIONS: frozenset[str] = frozenset({GIT_FETCH, GIT_PUSH})


def actions_valid_for_type(endpoint_type: str) -> frozenset[str]:
    """``plain`` has no forge vocabulary (no ``mr.*``/``pipeline.*``/``issue.*``);
    ``gitlab`` has all eight ids.

    ``github`` is rejected with ``ConfigError`` at config-parse time
    (``warden.core.config_load._RESERVED_ENDPOINT_TYPES``), so this function
    is never actually called with it from a loaded config; it still raises
    ``ValueError`` rather than inventing an action set for an unreachable type.
    """
    if endpoint_type == "plain":
        return _PLAIN_ACTIONS
    if endpoint_type == "gitlab":
        return ALL_ACTIONS
    if endpoint_type == "github":
        raise ValueError(
            f"endpoint type {endpoint_type!r} is not implemented yet "
            "(see warden.core.config_load._RESERVED_ENDPOINT_TYPES)"
        )
    raise ValueError(f"unknown endpoint type {endpoint_type!r}")


# Recognizer ids DEFAULT_ACTIONS' REST actions span, asserted below to equal
# DEFAULT_ENABLED.
_DEFAULT_REST_RECOGNIZERS: frozenset[str] = frozenset(
    recognizer_id
    for action in DEFAULT_ACTIONS
    for recognizer_id in ACTION_TO_RECOGNIZERS.get(action, ())
)
assert _DEFAULT_REST_RECOGNIZERS == DEFAULT_ENABLED, (
    "DEFAULT_ACTIONS' REST actions must span exactly DEFAULT_ENABLED's recognizers"
)
