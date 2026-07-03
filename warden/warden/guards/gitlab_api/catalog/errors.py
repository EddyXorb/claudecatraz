"""Catalog-specific errors.

Kept separate from :mod:`warden.core.config` so the catalog package never
imports ``core/config.py`` at module load time. A caller that wants a startup abort
catches :class:`CatalogConfigError` directly rather than re-raising it as
:class:`warden.core.config.ConfigError` — the two are independent siblings.
"""

from __future__ import annotations


class CatalogConfigError(ValueError):
    """Invalid endpoint-activation config: an unknown catalog id, an override
    that widens instead of narrows, an override for an endpoint that is not
    enabled, or an attempt to enable an entry whose capabilities intersect
    ``FORBIDDEN`` (no scoping-check taming mechanism exists yet)."""
