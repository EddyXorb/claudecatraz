"""JSON-serialisable summary of the effective endpoint table (§04.3;
docs/design/agentic-workflow/04-cli.md ``catraz doctor``/``allow-endpoint``).

Served by the admin ``/policy`` route (``app.py``) so the CLI (which never
imports ``warden`` — it only ships it as a container asset) can learn the
catalog's ids and the running stack's activation state without a runtime
Python import (A2: no code execution from config, and no code execution to
introspect config either).
"""

from __future__ import annotations

from typing import Any

from ....core.config import Config
from .entries import CATALOG, DEFAULT_ENABLED


def endpoint_table_report(cfg: Config) -> dict[str, Any]:
    """Build the ``/policy`` response body: every catalog entry, whether it
    is part of the shipped default set, and whether *this* deployment's
    config actually activated it (§04.3's "Default-Satz + Aktivierungen +
    Overrides" — ``catraz doctor`` prints exactly this).
    """
    table = cfg.effective_endpoints
    active_by_id = {e.id: e for e in table.entries}
    rows = []
    for entry in CATALOG:
        active_entry = active_by_id.get(entry.id)
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
                    {"name": f.name, "location": f.location.value}
                    for f in entry.decision_fields
                ],
            }
        )
    return {
        "catalog": rows,
        # The merge endpoint is never a catalog row (builtin.py) — surfaced
        # separately so a consumer (catraz doctor) can state it explicitly
        # rather than it just being absent from "catalog".
        "builtin_deny": ["mr.merge"],
    }
