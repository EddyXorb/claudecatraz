"""Endpoint types for the git namespace: guard composition + valid action ids.

``"github"`` stays a reserved, not-yet-implemented type — it is rejected in
``warden.core.config_load``, never listed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ...core.endpoints import EndpointType
from . import actions as git_actions


@dataclass(frozen=True)
class GitEndpointType:
    """One ``[[git.endpoint]] type`` value: its guard composition plus the
    action ids valid for it.

    ``valid_action_ids`` is hand-listed here only until 10-04, which derives
    it from the union of each composing guard's ``SUPPORTED`` instead.
    """

    type: EndpointType
    valid_action_ids: frozenset[str]  # TODO(10-04): derive from guards' SUPPORTED unions


_REPO_IDS: frozenset[str] = frozenset(
    action.id for action in git_actions.ALL if action.id.startswith("repo.")
)

ENDPOINT_TYPES: Mapping[str, GitEndpointType] = {
    "plain": GitEndpointType(
        type=EndpointType(name="plain", guards=("transport",)),
        valid_action_ids=_REPO_IDS,
    ),
    "gitlab": GitEndpointType(
        type=EndpointType(name="gitlab", guards=("transport", "gitlab")),
        valid_action_ids=frozenset(git_actions.by_id),
    ),
}
