"""JSON-serialisable summary of the effective actions + recognizer catalog, per host.

Served by the admin ``/policy`` route so the CLI can learn a configured
host's effective actions without a runtime Python import. Per-host, since two
hosts with different ``actions`` can genuinely differ.
"""

from __future__ import annotations

from typing import Any

from ....core.config import Config
from .recognizers import CATALOG


def endpoint_table_report(cfg: Config) -> dict[str, Any]:
    """Build the ``/policy`` response body: one section per configured host."""
    return {"hosts": {host: _host_report(cfg, host) for host in cfg.effective_hosts}}


def _host_report(cfg: Config, host: str) -> dict[str, Any]:
    actions = cfg.effective_actions(host)
    rows = [
        {
            "id": row.id,
            "methods": sorted(row.methods),
            "template": row.template,
            "quota_kind": row.quota_kind.value if row.quota_kind is not None else None,
        }
        for row in CATALOG
    ]
    return {"actions": list(actions), "catalog": rows}
