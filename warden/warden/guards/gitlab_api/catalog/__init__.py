"""The endpoint catalog package: data-driven endpoint definitions, activation, validation.

Public surface — ``guards.gitlab_api.policy``, ``guards.gitlab_api.guard``, ``__main__.py``,
and ``app.py`` import ``warden.guards.gitlab_api.catalog`` rather than submodules:

* ``model``      — data types: ``CatalogEntry``, ``FieldSpec``, ``Location``, …
* ``checks``      — the named Check registry
* ``entries``     — the catalog table + ``api_capabilities``/``match_endpoint``
* ``builtin``     — the merge endpoint's built-in deny invariant
* ``config_parse``— ``[api.endpoints]`` TOML shape parsing
* ``activation``  — Config × Catalog → effective, request-matchable table
"""

from __future__ import annotations

from .activation import EffectiveTable, build_effective_table
from .builtin import is_builtin_merge_endpoint
from .checks import CHECKS, MR_SOURCE_IN_NAMESPACE, field_has_prefix, field_not_equals
from .config_parse import ApiEndpointsConfig, parse_api_endpoints
from .entries import CATALOG, DEFAULT_ENABLED, api_capabilities, match_endpoint
from .errors import CatalogConfigError
from .model import CatalogEntry, EndpointKind, FieldSpec, Location, RegisteredCheck
from .report import endpoint_table_report

__all__ = [
    "CATALOG",
    "CHECKS",
    "ApiEndpointsConfig",
    "CatalogConfigError",
    "CatalogEntry",
    "DEFAULT_ENABLED",
    "EffectiveTable",
    "EndpointKind",
    "FieldSpec",
    "Location",
    "MR_SOURCE_IN_NAMESPACE",
    "RegisteredCheck",
    "api_capabilities",
    "build_effective_table",
    "endpoint_table_report",
    "field_has_prefix",
    "field_not_equals",
    "is_builtin_merge_endpoint",
    "match_endpoint",
    "parse_api_endpoints",
]
