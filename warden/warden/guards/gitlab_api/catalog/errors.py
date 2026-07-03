"""Catalog-specific errors (§04.2/04.3, docs/design/architecture-generalization,
§04-policy-erweiterbarkeit.md, §06-migration.md Schritt 4).

Kept separate from :mod:`warden.core.config` so the catalog package never
needs a module-load-time import of ``core/config.py``; ``core.config`` in
turn imports nothing from this package at all — the gitlab_api guard is the
only place that builds the effective table (``ApiGuard.__init__``,
``__main__.py``, ``catalog.report``). A caller that wants a startup abort
catches :class:`CatalogConfigError` directly (``__main__.py``'s composition
root does) rather than re-raising it as
:class:`warden.core.config.ConfigError` — the two are siblings, not one
wrapping the other, now that no ``Config`` property builds this lazily.
"""

from __future__ import annotations


class CatalogConfigError(ValueError):
    """Invalid endpoint-activation config (§04.3): an unknown catalog id, an
    override that widens instead of narrows, an override for an endpoint that
    is not enabled, or an attempt to enable an entry whose capabilities
    intersect ``FORBIDDEN`` (no scoping-check taming mechanism exists yet —
    §04.2's deliberate YAGNI, see ``04-policy-erweiterbarkeit.md``)."""


class StartgateFailure(RuntimeError):
    """A catalog deny-probe was ALLOWED by the effective policy (§04.4).

    The warden refuses to start rather than serve a policy that fails its own
    curated examples — data (the probe) can never open anything the code
    doesn't already allow (A2); this only catches a *code or config* mistake
    before it reaches a real request.
    """
