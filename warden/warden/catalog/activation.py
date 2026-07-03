"""Endpoint activation: Config × Catalog → the effective table (§04.2/04.3;
docs/design/architecture-generalization/04-policy-erweiterbarkeit.md §04.2/04.3,
§06-migration.md Schritt 4).

:func:`build_effective_table` is the one pure function this step's whole
security story rests on: it runs exactly once at startup
(``Config.effective_endpoints``), never again — ``policy``/``api_proxy`` match
requests against its output, never against :data:`~warden.catalog.entries.CATALOG`
directly (F4 hygiene: no runtime rebuild, no cache that could drift from the
config that produced it).

Fail-closed by construction: every branch below either returns a valid
:class:`EffectiveTable` or raises :class:`~warden.catalog.errors.CatalogConfigError`
— there is no partial/best-effort table.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from ..capabilities import FORBIDDEN
from ..config import Config
from .config_parse import EndpointActivation
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


def build_effective_table(cfg: Config, activation: EndpointActivation) -> EffectiveTable:
    """Build the effective table once. Raises :class:`CatalogConfigError` on
    any of the fail-closed validation rules from §04.3:

    * an unknown catalog id in ``enable`` or in ``overrides``,
    * an override for an id that is not (also) enabled,
    * an override that widens instead of narrows a default,
    * enabling an entry whose static capabilities intersect ``FORBIDDEN``
      (§04.2's deliberate YAGNI: no scoping-check taming mechanism exists yet).
    """
    catalog_by_id = {e.id: e for e in CATALOG}
    enable = activation.enable if activation.enable is not None else tuple(DEFAULT_ENABLED)

    unknown_enabled = sorted(set(enable) - set(catalog_by_id))
    if unknown_enabled:
        raise CatalogConfigError(
            f"warden.toml [api.endpoints].enable: unknown catalog id(s): "
            f"{', '.join(unknown_enabled)}"
        )

    enabled_set = set(enable)
    for entry_id in activation.overrides:
        if entry_id not in catalog_by_id:
            raise CatalogConfigError(
                f"warden.toml [api.endpoints.overrides]: unknown catalog id {entry_id!r}"
            )
        if entry_id not in enabled_set:
            raise CatalogConfigError(
                f"warden.toml [api.endpoints.overrides.{entry_id!r}]: {entry_id!r} is not "
                "in [api.endpoints].enable — overrides only apply to enabled entries"
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
        entry = _apply_overrides(entry, cfg, activation.overrides.get(entry_id, {}))
        entries.append(entry)
        enabled_via[entry_id] = "default" if entry_id in DEFAULT_ENABLED else f"config:{entry_id}"

    return EffectiveTable(entries=tuple(entries), enabled_via=enabled_via)


def _apply_overrides(
    entry: CatalogEntry, cfg: Config, overrides: Mapping[str, object]
) -> CatalogEntry:
    if not overrides:
        return entry
    by_key = {o.key: o for o in entry.overridable}
    checks = list(entry.checks)
    for key, value in overrides.items():
        knob = by_key.get(key)
        if knob is None:
            raise CatalogConfigError(
                f"warden.toml [api.endpoints.overrides.{entry.id!r}]: no overridable "
                f"parameter {key!r} on this entry"
            )
        if not knob.is_narrower(cfg, value):
            raise CatalogConfigError(
                f"warden.toml [api.endpoints.overrides.{entry.id!r}].{key} = {value!r} "
                "does not narrow the default — overrides may only restrict, never widen "
                "(§04.2/04.3)"
            )
        checks[knob.check_index] = knob.rebuild(cfg, value)
    return replace(entry, checks=tuple(checks))
