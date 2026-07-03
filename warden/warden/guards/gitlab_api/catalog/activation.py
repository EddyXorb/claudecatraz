"""Endpoint activation: Config × Catalog → the effective table (§04.2/04.3;
docs/design/architecture-generalization/04-policy-erweiterbarkeit.md §04.2/04.3,
§06-migration.md Schritt 4).

:func:`build_effective_table` is the one pure function this step's whole
security story rests on: it runs exactly once at startup (``ApiGuard.__init__``,
``__main__.py``), never again — the guard's policy/proxy code matches requests
against its output, never against
:data:`~warden.guards.gitlab_api.catalog.entries.CATALOG` directly (F4
hygiene: no runtime rebuild, no cache that could drift from the config that
produced it).

Fail-closed by construction: every branch below either returns a valid
:class:`EffectiveTable` or raises
:class:`~warden.guards.gitlab_api.catalog.errors.CatalogConfigError` — there
is no partial/best-effort table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from ....core.capabilities import FORBIDDEN
from ....core.config import Config
from .entries import CATALOG, DEFAULT_ENABLED
from .errors import CatalogConfigError
from .model import CatalogEntry


@dataclass(frozen=True)
class EffectiveTable:
    """The built, request-matchable endpoint table (§04.2).

    ``enabled_via`` marks every active entry with how it came to be active:
    ``"default"`` for the shipped default set, ``"config:<id>"`` for anything
    a deployment's ``warden.toml`` additionally enabled. The audit layer uses
    this to flag non-default activations via a dedicated field instead of
    overloading the ``rule`` id (§04.3 deviation from the sketch in
    ``04-policy-erweiterbarkeit.md`` — documented there).
    """

    entries: tuple[CatalogEntry, ...]
    enabled_via: Mapping[str, str]


def build_effective_table(cfg: Config, enable: Optional[tuple[str, ...]]) -> EffectiveTable:
    """Build the effective table once. Raises :class:`CatalogConfigError` on
    any of the fail-closed validation rules from §04.3:

    * an unknown catalog id in ``enable``,
    * enabling an entry whose static capabilities intersect ``FORBIDDEN``
      (§04.2's deliberate YAGNI: no scoping-check taming mechanism exists yet).

    ``enable`` is ``cfg.endpoint_enable`` — ``None`` means the ``[api.endpoints]``
    section (or the whole ``warden.toml``) was absent, falling back to the
    catalog's default set; an explicit empty tuple activates nothing.
    """
    catalog_by_id = {e.id: e for e in CATALOG}
    enable = enable if enable is not None else tuple(DEFAULT_ENABLED)

    unknown_enabled = sorted(set(enable) - set(catalog_by_id))
    if unknown_enabled:
        raise CatalogConfigError(
            f"warden.toml [api.endpoints].enable: unknown catalog id(s): "
            f"{', '.join(unknown_enabled)}"
        )

    entries: list[CatalogEntry] = []
    enabled_via: dict[str, str] = {}
    seen: set[str] = set()
    for entry_id in enable:
        if entry_id in seen:  # tolerate an accidental duplicate in the enable list
            continue
        seen.add(entry_id)
        entry = catalog_by_id[entry_id]
        forbidden_hit = entry.capabilities & FORBIDDEN
        if forbidden_hit:
            names = ", ".join(sorted(c.value for c in forbidden_hit))
            raise CatalogConfigError(
                f"warden.toml [api.endpoints].enable: {entry_id!r} declares forbidden "
                f"capabilities ({names}) — activation refused. No scoping-check taming "
                "mechanism exists yet for FORBIDDEN capabilities (§04.2 YAGNI); see "
                "docs/design/architecture-generalization/04-policy-erweiterbarkeit.md"
            )
        entries.append(entry)
        enabled_via[entry_id] = "default" if entry_id in DEFAULT_ENABLED else f"config:{entry_id}"

    return EffectiveTable(entries=tuple(entries), enabled_via=enabled_via)
