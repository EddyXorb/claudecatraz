"""JSON-serialisable summary of every guard's recognizer catalog, per host.

Served by the admin /policy route so the CLI can learn a configured
host's effective actions and catalog state without a runtime Python import.
One section per host; within a host, one sub-list per guard composing that
host's endpoint type (guards.git.endpoints.ENDPOINT_TYPES) — a plain
host shows only the transport guard's rows, a gitlab host shows both.

Every row is a recognizer from that guard's own recognizer table: its id,
the actions it can possibly recognize (with criticality, default membership,
whether currently active for this host, and its quota kind where one
applies). A row's action(s) at Criticality.IRREVERSIBLE are always
inactive by construction (the kernel's criticality gate denies them
regardless of config) — denials collects those ids per host so a never
class action is named explicitly instead of hidden inside a row.
"""

from __future__ import annotations

from typing import Any, Mapping

from ...core.actions import Criticality
from ...core.config import Config
from ...core.guard import Guard
from . import actions as git_actions
from .endpoints import ENDPOINT_TYPES
from .gitlab.guard import ApiGuard
from .transport.guard import GitGuard

_DEFAULT_IDS: frozenset[str] = frozenset(a.id for a in git_actions.DEFAULT)

# Endpoint-type guard-composition name -> the concrete guard class serving
# it. Fixed and small (one row per shipped guard) — a future namespace adds
# its own report module rather than growing this map indefinitely.
_GUARD_CLASS_BY_COMPOSITION_NAME: Mapping[str, type[Guard[Any]]] = {
    "transport": GitGuard,
    "gitlab": ApiGuard,
}


def _guards_by_composition_name(guards: list[Guard[Any]]) -> Mapping[str, Guard[Any]]:
    by_class = {type(g): g for g in guards}
    return {
        name: by_class[cls]
        for name, cls in _GUARD_CLASS_BY_COMPOSITION_NAME.items()
        if cls in by_class
    }


def endpoint_table_report(cfg: Config, guards: list[Guard[Any]]) -> dict[str, Any]:
    """Build the /policy response body: one section per configured host.

    guards is the running AppContext's guard instances — the report
    walks the actual objects handling requests, never a fresh set built for
    reporting only.
    """
    by_name = _guards_by_composition_name(guards)
    return {"hosts": {host: _host_report(cfg, host, by_name) for host in cfg.effective_hosts}}


def _host_report(cfg: Config, host: str, by_name: Mapping[str, Guard[Any]]) -> dict[str, Any]:
    effective = frozenset(cfg.effective_actions(host))
    endpoint = cfg.endpoint_for(host)
    assert endpoint is not None, "host comes from cfg.effective_hosts, so its endpoint exists"
    composition = ENDPOINT_TYPES[endpoint.type].type.guards

    rows: list[dict[str, Any]] = []
    denials: set[str] = set()
    for guard_name in composition:
        guard = by_name.get(guard_name)
        if guard is None:
            continue
        for recognizer in guard.recognizers:
            row_report, row_denials = _row_report(recognizer, effective)
            rows.append({"guard": guard_name, **row_report})
            denials |= row_denials

    # The "catalog" key is the /policy contract the CLI (catraz doctor) parses;
    # the local list is named rows to match the guard-level "recognizers".
    return {
        "actions": sorted(effective),
        "catalog": rows,
        "denials": sorted(denials),
    }


def _row_report(row: Any, effective: frozenset[str]) -> tuple[dict[str, Any], set[str]]:
    actions = []
    denials: set[str] = set()
    for action in sorted(row.possible_actions, key=lambda a: a.id):
        never = action.criticality is Criticality.IRREVERSIBLE
        if never:
            denials.add(action.id)
        actions.append(
            {
                "id": action.id,
                "criticality": action.criticality.name,
                "default": action.id in _DEFAULT_IDS,
                "active": action.id in effective and not never,
                "quota_kind": action.quota_kind,
            }
        )
    return ({"id": row.id, "actions": actions}, denials)
