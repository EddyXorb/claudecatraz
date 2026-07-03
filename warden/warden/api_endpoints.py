"""Compatibility facade (§06-migration.md Schritt 4).

The write-endpoint table moved to :mod:`warden.catalog` — the Check-Registry
(§04.1) and the Endpoint-Katalog + activation config (§04.2/04.3). This
module re-exports the pre-Schritt-4 names so any code still importing
``warden.api_endpoints`` keeps working; new code should import
``warden.catalog`` directly (``policy.py`` and ``api_proxy.py`` already do).

``WRITE_ENDPOINTS`` here is :data:`warden.catalog.entries.CATALOG` — the
*full* catalog, including entries no deployment activates by default. That is
a deliberate change from Schritt 3 (where this name held exactly the active
six rows): the active set for a given deployment is
``Config.effective_endpoints``, not a module-level constant — see
``catalog.activation.build_effective_table``.
"""

from __future__ import annotations

from .catalog import CatalogEntry as WriteEndpoint
from .catalog import EndpointKind, api_capabilities
from .catalog.checks import OWNED_BY_AGENT as _OWNED_BY_AGENT
from .catalog.checks import field_has_prefix
from .catalog.entries import CATALOG as WRITE_ENDPOINTS
from .catalog.entries import match_endpoint as _catalog_match_endpoint
from .model import Decision, ProxyRequest, StateView

__all__ = [
    "EndpointKind",
    "WriteEndpoint",
    "WRITE_ENDPOINTS",
    "api_capabilities",
    "field_has_prefix",
    "mr_owned_by_claude",
    "match_endpoint",
]


def mr_owned_by_claude(req: ProxyRequest, state: StateView, cfg: object) -> "Decision | None":
    """Compat shim for the pre-Schritt-4 name — delegates to the registry's
    ``owned_by_agent`` check (``catalog.checks.OWNED_BY_AGENT``, §04.1)."""
    return _OWNED_BY_AGENT.fn(req, state, cfg)  # type: ignore[arg-type]


def match_endpoint(method: str, path: str) -> "WriteEndpoint | None":
    """Compat shim: matches against the *full catalog*, not an effective
    table — callers that need activation-aware matching (the real request
    path) must use ``Config.effective_endpoints`` + ``catalog.match_endpoint``
    directly, as ``policy.py``/``api_proxy.py`` do.
    """
    return _catalog_match_endpoint(WRITE_ENDPOINTS, method, path)
