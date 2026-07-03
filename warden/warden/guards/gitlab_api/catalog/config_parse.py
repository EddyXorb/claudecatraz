"""Parse ``[api.endpoints]`` out of the raw ``warden.toml`` dict (§04.2/04.3;
docs/design/architecture-generalization/04-policy-erweiterbarkeit.md §04.2).

Deliberately free of any dependency on :mod:`warden.core.config` — not even
``ConfigError`` — so ``config.py`` can import this module (deferred, at call
time inside ``from_env``) without a load-time cycle. Malformed shapes raise
:class:`EndpointConfigError`, a plain ``ValueError`` subclass that
``config.py`` re-wraps as ``ConfigError`` at that boundary, the same way it
already re-wraps ``tomllib.TOMLDecodeError``.

This module only parses *shape* (is ``enable`` a list of strings?). Whether
an id actually exists in the catalog is checked later against the catalog
itself — see ``activation.build_effective_table`` — because that check needs
the catalog, which this module may not depend on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


class EndpointConfigError(ValueError):
    """Malformed ``[api.endpoints]`` shape."""


@dataclass(frozen=True)
class EndpointActivation:
    """Raw, catalog-agnostic shape of the ``[api.endpoints]`` config.

    ``enable`` is ``None`` when the section (or the whole file) is absent —
    the caller (``activation.build_effective_table``) then falls back to the
    catalog's default set, never an empty list. An *explicit* ``enable = []``
    and an *absent* section must stay distinguishable, or a bare
    ``[api.endpoints]`` table would silently disable every default entry
    (§04.3 behaviour preservation).
    """

    enable: Optional[tuple[str, ...]] = None


def parse_endpoint_activation(file: Mapping[str, object]) -> EndpointActivation:
    """Parse the ``[api.endpoints]`` table out of a loaded ``warden.toml`` dict."""
    api = file.get("api", {})
    if not isinstance(api, Mapping):
        raise EndpointConfigError("warden.toml: [api] must be a table")
    endpoints = api.get("endpoints", {})
    if not isinstance(endpoints, Mapping):
        raise EndpointConfigError("warden.toml: [api.endpoints] must be a table")
    if not endpoints:
        return EndpointActivation()

    enable: Optional[tuple[str, ...]] = None
    if "enable" in endpoints:
        enable_raw = endpoints["enable"]
        if not isinstance(enable_raw, list) or not all(isinstance(x, str) for x in enable_raw):
            raise EndpointConfigError(
                "warden.toml: [api.endpoints].enable must be a list of strings"
            )
        enable = tuple(enable_raw)

    return EndpointActivation(enable=enable)
