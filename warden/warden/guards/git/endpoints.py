"""Endpoint types for the git namespace: guard composition + valid action ids.

A type's valid action ids are the union of its composing guards' own
SUPPORTED sets, accessed qualified — this module and core stay
guard-class-free."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from ...core.actions import Action
from ...core.config import Config, GitEndpoint
from ...core.endpoints import EndpointType
from ...core.transport import UpstreamLayout
from .github import actions as github_actions
from .gitlab import actions as gitlab_actions
from .transport import actions as transport_actions


@dataclass(frozen=True)
class GitEndpointType:
    """One [[git.endpoint]] type value: its guard composition, the action
    ids valid for it, and where its upstream surfaces live (from the host).
    """

    type: EndpointType
    valid_action_ids: frozenset[str]
    layout: Callable[[str], UpstreamLayout]


_SUPPORTED_BY_GUARD: Mapping[str, frozenset[Action]] = {
    "transport": transport_actions.SUPPORTED,
    "gitlab": gitlab_actions.SUPPORTED,
    "github": github_actions.SUPPORTED,
}


def _valid_action_ids(guards: tuple[str, ...]) -> frozenset[str]:
    """Union of each named guard's SUPPORTED action ids."""
    supported: frozenset[Action] = frozenset()
    for name in guards:
        supported |= _SUPPORTED_BY_GUARD[name]
    return frozenset(action.id for action in supported)


def _gitlab_layout(host: str) -> UpstreamLayout:
    return UpstreamLayout(
        git_base=f"https://{host}",
        api_base=f"https://{host}/api/v4",
        rest_auth_header="PRIVATE-TOKEN",
        rest_auth_scheme="",
        git_user="oauth2",
    )


def _plain_layout(host: str) -> UpstreamLayout:
    return UpstreamLayout(
        git_base=f"https://{host}",
        api_base=None,
        rest_auth_header="",
        rest_auth_scheme="",
        git_user="oauth2",
    )


def _github_layout(host: str) -> UpstreamLayout:
    """github.com and GHE.com data-residency hosts serve REST on an api.
    subdomain; GitHub Enterprise Server serves it under /api/v3."""
    bare = Config.normalize_host(host)
    hosted = bare == "github.com" or bare.endswith(".ghe.com")
    host = host.strip().lower().rstrip(".")  # GitHub's Link URLs are lower-case
    return UpstreamLayout(
        git_base=f"https://{host}",
        api_base=f"https://api.{host}" if hosted else f"https://{host}/api/v3",
        rest_auth_header="Authorization",
        rest_auth_scheme="Bearer ",
        git_user="x-access-token",
    )


def _endpoint_type(
    name: str, guards: tuple[str, ...], layout: Callable[[str], UpstreamLayout]
) -> GitEndpointType:
    return GitEndpointType(
        type=EndpointType(name=name, guards=guards),
        valid_action_ids=_valid_action_ids(guards),
        layout=layout,
    )


ENDPOINT_TYPES: Mapping[str, GitEndpointType] = {
    "plain": _endpoint_type("plain", ("transport",), _plain_layout),
    "gitlab": _endpoint_type("gitlab", ("transport", "gitlab"), _gitlab_layout),
    "github": _endpoint_type("github", ("transport", "github"), _github_layout),
}


def upstream_layout(endpoint: GitEndpoint) -> UpstreamLayout:
    """The UpstreamRouter's layout_for: the endpoint's type decides the layout."""
    return ENDPOINT_TYPES[endpoint.type].layout(endpoint.host)


def composes(cfg: Config, host: str, guard: str) -> bool:
    """True iff host's configured endpoint type is composed of guard.

    An unconfigured host composes nothing, so a guard asking never serves it.
    """
    endpoint = cfg.endpoint_for(host)
    return endpoint is not None and guard in ENDPOINT_TYPES[endpoint.type].type.guards


def open_hosts_composing(cfg: Config, guard: str) -> tuple[str, ...]:
    """cfg.open_hosts narrowed to the hosts whose type is composed of guard —
    what that guard's reconcile iterates."""
    return tuple(host for host in cfg.open_hosts if composes(cfg, host, guard))
