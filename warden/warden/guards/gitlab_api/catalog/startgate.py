"""The startgate: Policy-by-Example as a gate, not a mechanism.

Every activated catalog entry's deny-probes, plus built-in invariants' global
probes, run against the effective policy (``guards.gitlab_api.policy.full_decide``)
before the warden opens a port. A probe that would be *allowed* means the code or
config has a bug serious enough to abort the boot.

No network, no state DB: probes run against a synthetic, always-unlocked
:class:`~warden.core.model.StateView` and a ``Config`` copy whose allowlist is
widened just enough to let each probe reach the entry's *own* checks.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import replace

from ....core.config import Config
from ....core.model import StateView
from ..intent import ApiIntent
from .activation import EffectiveTable
from .builtin import BUILTIN_DENY_PROBES
from .errors import StartgateFailure
from .model import PROBE_PROJECT, DenyProbe
from .probes import ENTRY_DENY_PROBES

# Mirrors guards.gitlab_api.parsing.project_from_path exactly — duplicated
# rather than imported to avoid a load-time cycle (parsing.py imports the
# catalog for CatalogEntry/Location; this is a 2-line regex, not worth
# coupling the two modules over).
_PROJECT_RE = re.compile(r"/projects/([^/]+)")


def _project_from_probe_path(path: str) -> str:
    m = _PROJECT_RE.search(path)
    return urllib.parse.unquote(m.group(1)) if m else ""


def _probe_config(cfg: Config) -> Config:
    """A Config that mirrors *cfg*'s policy but always allowlists :data:`PROBE_PROJECT`
    — every probe exercises the entry's own checks, not an incidental R6 project-boundary deny.
    """
    return replace(cfg, allowed_projects=(PROBE_PROJECT,))


def _probe_intent(probe: DenyProbe) -> ApiIntent:
    project = _project_from_probe_path(probe.path)
    return ApiIntent(
        _project=project,
        _method=probe.method,
        path=probe.path,
        fields=dict(probe.fields),
        mr_owner_ok=probe.mr_owner_ok,
    )


def _run_probe(cfg: Config, effective: EffectiveTable, entry_id: str, probe: DenyProbe) -> None:
    # Deferred import to avoid circular dependency between catalog and policy modules.
    from ..policy import full_decide

    d = full_decide(_probe_intent(probe), StateView(), cfg, effective)
    if d.allow:
        raise StartgateFailure(
            f"catalog entry {entry_id!r}: deny-probe {probe.description!r} "
            f"({probe.method} {probe.path}) was ALLOWED by the effective policy "
            "— refusing to start"
        )


def run_startgate(cfg: Config, table: EffectiveTable) -> None:
    """Run every activated entry's deny-probes, plus built-in global probes,
    against the effective policy — the *table* itself, not freshly rebuilt
    from ``cfg.endpoint_enable``. Raises :class:`StartgateFailure` on the first
    probe that would be allowed.
    """
    probe_cfg = _probe_config(cfg)
    for entry in table.entries:
        for probe in ENTRY_DENY_PROBES.get(entry.id, ()):
            _run_probe(probe_cfg, table, entry.id, probe)
    for probe in BUILTIN_DENY_PROBES:
        _run_probe(probe_cfg, table, "<builtin>", probe)
