"""Endpoint-catalog CLI support for catraz doctor: fetches the /policy admin route.

Catalog ids and their meaning are only known to the running warden; catraz
never imports warden's Python itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from catraz.admin_client import get_json


def fetch_policy_report(root: Path) -> dict[str, Any]:
    """Fetch the running warden's /policy report: one section per configured host."""
    report: dict[str, Any] = get_json(root, "/policy")
    return report
