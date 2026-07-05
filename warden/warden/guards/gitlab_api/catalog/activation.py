"""Endpoint activation: a host's effective **actions** × Catalog → the effective table.

:func:`build_effective_table` is the one pure function this step's whole
security story rests on: it runs exactly once per host at startup
(``ApiGuard.__init__``), never again — the guard's policy/proxy code matches
requests against its output, never against the catalog directly. No runtime
rebuild, no drift.

Fail-closed by construction: every branch below either returns a valid
:class:`EffectiveTable` or raises
:class:`~warden.guards.gitlab_api.catalog.errors.CatalogConfigError` — there
is no partial/best-effort table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ....core.capabilities import FORBIDDEN
from .errors import CatalogConfigError
from .model import Recognizer
from .write_endpoints import WRITE_ENDPOINTS


@dataclass(frozen=True)
class EffectiveTable:
    """The built, request-matchable endpoint table.

    ``enabled_via`` marks every active entry with how it came to be active:
    ``"default"`` for the shipped Built-in-Default action set,
    ``"config:<action id>"`` for anything a deployment's ``actions`` list
    additionally activated. The audit layer uses this to flag non-default
    activations.
    """

    entries: tuple[Recognizer, ...]
    enabled_via: Mapping[str, str]


#: The table for a host with no configured endpoint: matches
#: nothing, so every write default-denies. Only reachable before the kernel's
#: ``host_gate`` has fired — see ``ApiGuard._table_for``'s docstring.
EMPTY_TABLE = EffectiveTable(entries=(), enabled_via={})


def build_effective_table(actions: tuple[str, ...]) -> EffectiveTable:
    """Build one host's effective table from its effective actions
    (``Config.effective_actions(host)``, new git-namespace ids). Raises
    :class:`CatalogConfigError` on validation failures:

    * an action id outside the closed vocabulary — a defence-in-depth
      backstop: the config loader already rejects this at startup, but this
      function never trusts a caller that bypassed it (e.g. a hand-built
      ``Config`` in a test);
    * activating a recognizer whose static capabilities intersect ``FORBIDDEN``
      (no scoping-check taming mechanism exists yet).

    Ids with no REST recognizer behind them (``repo.read``, ``repo.branch.push``,
    ``project.read``, ``instance.*``, …) are silently skipped, never an error —
    they are the git transport guard's or the read path's concern.
    """
    # Deferred imports: both tables are guard-owned — the catalog package
    # stays decoupled from them at module load time (matches
    # `core.config.effective_actions`'s deferred-import rationale).
    from ...git.actions import DEFAULT as default_actions
    from ...git.actions import by_id as all_action_ids
    from ..actions import _BRIDGE_10_03_RECOGNIZER_TO_ACTION

    default_ids = frozenset(action.id for action in default_actions)
    catalog_by_id = {e.id: e for e in WRITE_ENDPOINTS}
    recognizers_for_action: dict[str, list[str]] = {}
    for recognizer_id, action_id in _BRIDGE_10_03_RECOGNIZER_TO_ACTION.items():
        recognizers_for_action.setdefault(action_id, []).append(recognizer_id)

    entries: list[Recognizer] = []
    enabled_via: dict[str, str] = {}
    for action_id in actions:
        if action_id not in all_action_ids:
            raise CatalogConfigError(f"unknown action id {action_id!r}")
        for recognizer_id in recognizers_for_action.get(action_id, ()):
            if recognizer_id in enabled_via:  # tolerate an accidental duplicate action
                continue
            entry = catalog_by_id[recognizer_id]
            forbidden_hit = entry.capabilities & FORBIDDEN
            if forbidden_hit:
                names = ", ".join(sorted(c.value for c in forbidden_hit))
                raise CatalogConfigError(
                    f"action {action_id!r} activates recognizer {recognizer_id!r}, which "
                    f"declares forbidden capabilities ({names}) — activation refused. No "
                    "scoping-check taming mechanism exists yet for FORBIDDEN capabilities."
                )
            entries.append(entry)
            enabled_via[recognizer_id] = (
                "default" if action_id in default_ids else f"config:{action_id}"
            )

    return EffectiveTable(entries=tuple(entries), enabled_via=enabled_via)
