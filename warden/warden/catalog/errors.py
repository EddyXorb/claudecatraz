"""Catalog-specific errors (§04.2/04.3, docs/design/architecture-generalization,
§04-policy-erweiterbarkeit.md, §06-migration.md Schritt 4).

Kept separate from :mod:`warden.config` so the catalog package never needs a
module-load-time import of ``config.py`` (and vice versa — ``config.py`` only
reaches into the catalog lazily, at call time; see its ``from_env`` and
``Config.effective_endpoints``). A caller that wants a startup abort catches
these and re-raises :class:`warden.config.ConfigError` at that boundary,
exactly like ``tomllib.TOMLDecodeError`` is translated today.
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
