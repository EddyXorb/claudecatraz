"""The endpoint catalog package (§04.1-04.4; docs/design/architecture-generalization,
§04-policy-erweiterbarkeit.md, §06-migration.md Schritt 4).

Public surface other modules should import from — ``guards.gitlab_api.policy``,
``guards.gitlab_api.guard``, ``__main__.py`` and ``app.py`` all import
``warden.guards.gitlab_api.catalog`` rather than reaching into a submodule
directly:

* ``model``      — the data types (``CatalogEntry``, ``FieldSpec``, ``Location``, …)
* ``checks``      — the named Check registry (§04.1)
* ``entries``     — the catalog table itself (§04.2) + ``api_capabilities``/``match_endpoint``
* ``builtin``     — the non-catalog merge deny invariant (§04.2)
* ``config_parse``— ``[api.endpoints]`` TOML shape parsing (no Config dependency)
* ``activation``  — Config × Catalog → the effective, request-matchable table (§04.3)
* ``probes``      — deny-probes per entry id, kept out of the ``entries`` table (§04.4)
* ``startgate``   — runs every activated entry's deny-probes at boot (§04.4)
"""

from __future__ import annotations

from .activation import EffectiveTable, build_effective_table
from .builtin import BUILTIN_DENY_PROBES, is_builtin_merge_endpoint
from .checks import CHECKS, OWNED_BY_AGENT, field_has_prefix, field_not_equals
from .config_parse import EndpointActivation, EndpointConfigError, parse_endpoint_activation
from .entries import CATALOG, DEFAULT_ENABLED, api_capabilities, match_endpoint
from .errors import CatalogConfigError, StartgateFailure
from .model import (
    OTHER_PROJECT,
    PROBE_PROJECT,
    CatalogEntry,
    DenyProbe,
    EndpointKind,
    FieldSpec,
    Location,
    RegisteredCheck,
)
from .probes import ENTRY_DENY_PROBES
from .report import endpoint_table_report
from .startgate import run_startgate

__all__ = [
    "BUILTIN_DENY_PROBES",
    "CATALOG",
    "CHECKS",
    "CatalogConfigError",
    "CatalogEntry",
    "DEFAULT_ENABLED",
    "DenyProbe",
    "EffectiveTable",
    "ENTRY_DENY_PROBES",
    "EndpointActivation",
    "EndpointConfigError",
    "EndpointKind",
    "FieldSpec",
    "Location",
    "OTHER_PROJECT",
    "OWNED_BY_AGENT",
    "PROBE_PROJECT",
    "RegisteredCheck",
    "StartgateFailure",
    "api_capabilities",
    "build_effective_table",
    "endpoint_table_report",
    "field_has_prefix",
    "field_not_equals",
    "is_builtin_merge_endpoint",
    "match_endpoint",
    "parse_endpoint_activation",
    "run_startgate",
]
