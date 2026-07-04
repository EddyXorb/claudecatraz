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
    ``"default"`` for the shipped Built-in-Default action set (§09 §1.2),
    ``"config:<action id>"`` for anything a deployment's ``actions`` list
    additionally activated. The audit layer uses this to flag non-default
    activations.
    """

    entries: tuple[Recognizer, ...]
    enabled_via: Mapping[str, str]


#: The table for a host with no configured endpoint (§09 §3, step 03): matches
#: nothing, so every write default-denies. Only reachable before the kernel's
#: ``host_gate`` has fired — see ``ApiGuard._table_for``'s docstring.
EMPTY_TABLE = EffectiveTable(entries=(), enabled_via={})


def build_effective_table(actions: tuple[str, ...]) -> EffectiveTable:
    """Build one host's effective table from its effective actions
    (``Config.effective_actions(host)``, §09 §1.4). Raises
    :class:`CatalogConfigError` on validation failures:

    * an action id outside the closed vocabulary (§09 §3.1) — a defence-in-depth
      backstop: the config loader already rejects this at startup, but this
      function never trusts a caller that bypassed it (e.g. a hand-built
      ``Config`` in a test);
    * activating a recognizer whose static capabilities intersect ``FORBIDDEN``
      (no scoping-check taming mechanism exists yet, §2 Punkt 1).

    Only the **REST** (forge) actions in ``actions`` are relevant here —
    ``git.fetch``/``git.push`` are transport-only verbs the git guard's own
    action gate consumes; they are silently skipped, never an error.
    """
    # Deferred import: the action catalog lives in the guard's own `actions`
    # module, one level up from this `catalog` package — importing it at
    # module load time here would tie the catalog package to it eagerly for
    # no benefit (matches `core.config.effective_actions`'s deferred-import
    # rationale: the catalog is guard-owned).
    from ..actions import ACTION_TO_RECOGNIZERS, ALL_ACTIONS, DEFAULT_ACTIONS, FORGE_ACTIONS

    catalog_by_id = {e.id: e for e in WRITE_ENDPOINTS}

    entries: list[Recognizer] = []
    enabled_via: dict[str, str] = {}
    for action_id in actions:
        if action_id not in FORGE_ACTIONS:
            if action_id not in ALL_ACTIONS:
                raise CatalogConfigError(f"unknown action id {action_id!r}")
            continue  # a valid transport verb (git.fetch/git.push) — the git guard's concern
        for recognizer_id in ACTION_TO_RECOGNIZERS[action_id]:
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
                "default" if action_id in DEFAULT_ACTIONS else f"config:{action_id}"
            )

    return EffectiveTable(entries=tuple(entries), enabled_via=enabled_via)
