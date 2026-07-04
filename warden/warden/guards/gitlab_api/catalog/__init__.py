"""The endpoint catalog package: data-driven endpoint definitions, activation, validation.

Public surface — ``guards.gitlab_api.policy``, ``guards.gitlab_api.guard``, ``__main__.py``,
and ``app.py`` import ``warden.guards.gitlab_api.catalog`` rather than submodules:

* ``model``      — data types: ``Recognizer``, ``ScopeKind``, ``ReadClass``,
  ``FieldSpec``, ``Location``, …
* ``entries``     — the write catalog table + ``api_capabilities``/``match_endpoint``
* ``builtin``     — the merge endpoint's built-in deny invariant
* ``activation``  — a host's effective actions × Catalog → effective, request-matchable table

§07 Punkt 7 unified the former write-only ``CatalogEntry``/check-tuple shape
and the read-table's always-terminal ``ReadCheck`` shape into one type,
:class:`~.model.Recognizer` — see its docstring for the closed scope
vocabulary (``ScopeKind``) the one generic ``policy.decide_scope`` consumes.
"""

from __future__ import annotations

from .activation import EMPTY_TABLE, EffectiveTable, build_effective_table
from .builtin import is_builtin_merge_endpoint
from .errors import CatalogConfigError
from .model import ClassifyFn, EndpointKind, FieldSpec, Location, ReadClass, Recognizer, ScopeKind
from .read_endpoints import READ_ENDPOINTS
from .report import endpoint_table_report
from .write_endpoints import DEFAULT_ENABLED, WRITE_ENDPOINTS, api_capabilities, match_endpoint

__all__ = [
    "WRITE_ENDPOINTS",
    "READ_ENDPOINTS",
    "CatalogConfigError",
    "ClassifyFn",
    "DEFAULT_ENABLED",
    "EMPTY_TABLE",
    "EffectiveTable",
    "EndpointKind",
    "FieldSpec",
    "Location",
    "ReadClass",
    "Recognizer",
    "ScopeKind",
    "api_capabilities",
    "build_effective_table",
    "endpoint_table_report",
    "is_builtin_merge_endpoint",
    "match_endpoint",
]
