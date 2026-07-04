"""Endpoint-catalog CLI support for ``catraz doctor``: the ``/policy``
admin-route fetch.

Catalog ids and their meaning are only known to the running warden — catraz
never imports warden's Python, it only ships it as a container asset (see
pyproject.toml's force-include). This module only fetches the live
``/policy`` report (``admin_client.get_json``); ``catraz.doctor`` formats it.

Endpoint activation is each host's ``actions`` in ``warden.toml``'s
``[git]``/``[[git.endpoint]]`` tables, which ``catraz init`` scaffolds and
``catraz doctor`` cross-checks — there is no CLI-writable equivalent here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from catraz.admin_client import get_json


def fetch_policy_report(root: Path) -> dict[str, Any]:
    """Fetch the running warden's ``/policy`` report: one section per
    configured host, each with that host's effective actions + catalog
    activation state. Raises :class:`catraz.admin_client.AdminUnreachable` if
    the stack isn't up."""
    report: dict[str, Any] = get_json(root, "/policy")
    return report
