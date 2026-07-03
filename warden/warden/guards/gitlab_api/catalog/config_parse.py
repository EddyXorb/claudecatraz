"""Parse ``[api.endpoints]`` out of the raw ``warden.toml`` dict.

:class:`ApiEndpointsConfig` is the schema — decoded generically by
:func:`warden.core.toml_codec.decode` rather than a hand-rolled parser.
Malformed shapes raise :class:`warden.core.config.ConfigError` directly.

This module only parses *shape* (is ``enable`` a list of strings?). Whether
an id actually exists in the catalog is checked later in ``activation.build_effective_table``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from ....core import toml_codec
from ....core.config import ConfigError


@dataclass(frozen=True)
class ApiEndpointsConfig:
    """Raw, catalog-agnostic shape of the ``[api.endpoints]`` config.

    ``enable`` is ``None`` when the section (or the whole file) is absent — the caller
    then falls back to the catalog's default set, never an empty list.
    An *explicit* ``enable = []`` and an *absent* section must stay distinguishable.
    """

    enable: Optional[tuple[str, ...]] = None


def parse_api_endpoints(file: Mapping[str, object]) -> ApiEndpointsConfig:
    """Parse the ``[api.endpoints]`` table out of a loaded ``warden.toml`` dict.

    Absent ``[api]`` or ``[api.endpoints]`` ⇒ ``ApiEndpointsConfig()`` (the
    "use the catalog default set" marker) — only a *present* table is handed
    to the decoder, so an absent section never has to satisfy any required
    field.
    """
    api = file.get("api", {})
    if not isinstance(api, Mapping):
        raise ConfigError("warden.toml: [api] must be a table")
    endpoints = api.get("endpoints", {})
    if not isinstance(endpoints, Mapping):
        raise ConfigError("warden.toml: [api.endpoints] must be a table")
    if not endpoints:
        return ApiEndpointsConfig()
    return toml_codec.decode(ApiEndpointsConfig, endpoints, path="api.endpoints")
