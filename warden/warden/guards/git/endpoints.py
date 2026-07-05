"""Endpoint types for the git namespace: guard composition + valid action ids.

``"github"`` stays a reserved, not-yet-implemented type — it is rejected in
``warden.core.config_load``, never listed here.

A type's valid action ids are the union of its composing guards' own
``SUPPORTED`` sets (``transport.actions.SUPPORTED``/``gitlab.actions.SUPPORTED``
— each guard's own vocabulary module, qualified access, never a guard class:
this module and core stay guard-class-free, config validation needs these
ids before any guard is ever instantiated).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ...core.actions import Action
from ...core.endpoints import EndpointType
from .gitlab import actions as gitlab_actions
from .transport import actions as transport_actions


@dataclass(frozen=True)
class GitEndpointType:
    """One ``[[git.endpoint]] type`` value: its guard composition plus the
    action ids valid for it.
    """

    type: EndpointType
    valid_action_ids: frozenset[str]


_SUPPORTED_BY_GUARD: Mapping[str, frozenset[Action]] = {
    "transport": transport_actions.SUPPORTED,
    "gitlab": gitlab_actions.SUPPORTED,
}


def _valid_action_ids(guards: tuple[str, ...]) -> frozenset[str]:
    """Union of each named guard's ``SUPPORTED`` action ids."""
    supported: frozenset[Action] = frozenset()
    for name in guards:
        supported |= _SUPPORTED_BY_GUARD[name]
    return frozenset(action.id for action in supported)


def _endpoint_type(name: str, guards: tuple[str, ...]) -> GitEndpointType:
    return GitEndpointType(
        type=EndpointType(name=name, guards=guards), valid_action_ids=_valid_action_ids(guards)
    )


ENDPOINT_TYPES: Mapping[str, GitEndpointType] = {
    "plain": _endpoint_type("plain", ("transport",)),
    "gitlab": _endpoint_type("gitlab", ("transport", "gitlab")),
}
