"""Action catalog, forge side: Action-ID → Recognizer set, plus the
Built-in-Default and the ``type``-dependent vocabulary (§09 §1.2, §3.2, §5).

An action is a **code-defined, closed** group of recognizers — Agent
granularity, not wire granularity (§1.2): a deployment that enables
``mr.note`` but forgets ``mr.discussion_reply`` would have an agent that
cannot reply to review threads. ``ACTION_TO_RECOGNIZERS`` makes that mistake
impossible by construction. The mapping is **not** configurable and never
finer than a recognizer (``mr.update`` stays one block — field-dependent
distinctions such as ``state_event=merge`` are the capability layer's job,
never this one's).

Import-time consistency check: every recognizer id referenced here must
exist in :data:`~.catalog.write_endpoints.WRITE_ENDPOINTS`, and every
``WRITE_ENDPOINTS`` id must be covered by **exactly one** action — no
orphaned, no double-mapped recognizer. A violation is a programmer error
(the two tables were edited out of step), so it raises ``AssertionError`` at
import, never a ``ConfigError`` — this is a coding-time invariant, not a
deployment-time one.

Nothing here consumes config or wires into a guard yet — wiring is 02
(config validation), 03 (REST guard) and 03 (git guard).
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

# Action-ID -> the recognizer ids (from WRITE_ENDPOINTS) it covers (§1.2 table).
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

# The Built-in-Default (§1.2 right column): today's DEFAULT_ENABLED plus the
# two transport verbs. A warden.toml with no `actions` key at all behaves
# exactly like today — absent key != empty list (that distinction lives in 02).
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
    """The closed action vocabulary valid for an endpoint's ``type`` (§3.2).

    ``plain`` has no forge vocabulary (no ``mr.*``/``pipeline.*``/``issue.*``);
    ``gitlab`` has all eight ids.

    ``github`` is a reserved-but-unimplemented type, treated exactly like 08
    already treats it: ``warden.core.config_load._RESERVED_ENDPOINT_TYPES``
    rejects ``type = "github"`` with a ``ConfigError`` at config-parse time,
    before a ``GitEndpoint``/``Config`` value with that type can ever exist —
    so this function can never actually be called with ``"github"`` from a
    loaded config. It still refuses to "wave it through": calling it with
    ``"github"`` raises ``ValueError``, the same "known, not yet reachable"
    treatment, rather than inventing a made-up action set for a type that
    never reaches this far.
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


# Recognizer ids the Built-in-Default's REST actions span — used to prove
# (by test) that DEFAULT_ACTIONS behaves exactly like today's DEFAULT_ENABLED.
_DEFAULT_REST_RECOGNIZERS: frozenset[str] = frozenset(
    recognizer_id
    for action in DEFAULT_ACTIONS
    for recognizer_id in ACTION_TO_RECOGNIZERS.get(action, ())
)
assert _DEFAULT_REST_RECOGNIZERS == DEFAULT_ENABLED, (
    "DEFAULT_ACTIONS' REST actions must span exactly DEFAULT_ENABLED's recognizers"
)
