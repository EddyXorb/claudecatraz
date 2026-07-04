"""JSON-serialisable summary of the effective endpoint tables, per host.

Served by the admin ``/policy`` route (``app.py``) so the CLI can learn the
catalog's ids and each configured host's activation state without a runtime
Python import. Per-host, since two hosts with different ``actions`` can
genuinely differ: the report has one section per configured host rather
than a single flat table.
"""

from __future__ import annotations

from typing import Any

from ....core.config import Config
from .activation import build_effective_table
from .write_endpoints import DEFAULT_ENABLED, WRITE_ENDPOINTS


def endpoint_table_report(cfg: Config) -> dict[str, Any]:
    """Build the ``/policy`` response body: one section per configured host,
    each listing that host's effective actions and every catalog entry with
    whether it is part of the shipped default set and whether this host's
    config actually activated it.
    """
    return {
        "hosts": {host: _host_report(cfg, host) for host in cfg.effective_hosts},
        # The merge endpoint is never a catalog row (builtin.py) — surfaced
        # separately so a consumer (catraz doctor) can state it explicitly
        # rather than it just being absent from "catalog".
        "builtin_deny": ["mr.merge"],
    }


def _host_report(cfg: Config, host: str) -> dict[str, Any]:
    actions = cfg.effective_actions(host)
    table = build_effective_table(actions)
    active_by_id = {e.id: e for e in table.entries}
    rows = []
    for entry in WRITE_ENDPOINTS:
        active_entry = active_by_id.get(entry.id)
        assert entry.kind is not None, f"write catalog entry {entry.id!r} has no kind"
        rows.append(
            {
                "id": entry.id,
                "method": entry.method,
                "template": entry.template,
                "kind": entry.kind.value,
                "rule": entry.rule,
                "capabilities": sorted(c.value for c in entry.capabilities),
                "default": entry.id in DEFAULT_ENABLED,
                "active": active_entry is not None,
                "enabled_via": table.enabled_via.get(entry.id),
                "decision_fields": [
                    {"name": f.name, "location": f.location.value} for f in entry.decision_fields
                ],
            }
        )
    return {"actions": list(actions), "catalog": rows}
