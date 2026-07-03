"""Parse ``[api.endpoints]`` out of the raw ``warden.toml`` dict (§04.2/04.3;
docs/design/architecture-generalization/04-policy-erweiterbarkeit.md §04.2).

:class:`ApiEndpointsConfig` is the schema — decoded generically by
:func:`warden.core.toml_codec.decode` rather than a hand-rolled parser.
Malformed shapes raise :class:`warden.core.config.ConfigError` directly (the
same exception the decoder itself raises), so callers no longer need to
catch and re-wrap a catalog-local error at this boundary.

This module only parses *shape* (is ``enable`` a list of strings?). Whether
an id actually exists in the catalog is checked later against the catalog
itself — see ``activation.build_effective_table`` — because that check needs
the catalog, which this module may not depend on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from ....core import toml_codec
from ....core.config import ConfigError


@dataclass(frozen=True)
class ApiEndpointsConfig:
    """Raw, catalog-agnostic shape of the ``[api.endpoints]`` config.

    ``enable`` is ``None`` when the section (or the whole file) is absent —
    the caller (``activation.build_effective_table``) then falls back to the
    catalog's default set, never an empty list. An *explicit* ``enable = []``
    and an *absent* section must stay distinguishable, or a bare
    ``[api.endpoints]`` table would silently disable every default entry
    (§04.3 behaviour preservation).
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
