"""The startgate (§04.4; docs/design/architecture-generalization,
§04-policy-erweiterbarkeit.md §04.4, §06-migration.md Schritt 4/5).

Policy-by-Example as a *gate*, not a mechanism: every activated catalog
entry's deny-probes, plus the built-in invariants' global probes, run against
the effective, pure policy (``guards.gitlab_api.policy.full_decide`` — the
mode/project/capability gates the kernel enforces at runtime, composed with
this guard's own ``decide``, §06 Migrationsschritt 5) before the warden opens
a port. A probe that would be *allowed* means the code or config has a bug
serious enough to abort the boot — no request has ever been served with it.

No network, no state DB: probes run against a synthetic, always-unlocked
:class:`~warden.core.model.StateView` and a ``Config`` copy whose allowlist is
widened just enough to let each probe reach the entry's *own* checks (see
:data:`~warden.guards.gitlab_api.catalog.model.PROBE_PROJECT`) — a probe
proving the project boundary itself uses the deliberately-not-allowlisted
:data:`~warden.guards.gitlab_api.catalog.model.OTHER_PROJECT` instead.
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

# Mirrors guards.gitlab_api.parsing.project_from_path exactly — duplicated
# rather than imported to avoid a load-time cycle (parsing.py imports the
# catalog for CatalogEntry/Location; this is a 2-line regex, not worth
# coupling the two modules over).
_PROJECT_RE = re.compile(r"/projects/([^/]+)")


def _project_from_probe_path(path: str) -> str:
    m = _PROJECT_RE.search(path)
    return urllib.parse.unquote(m.group(1)) if m else ""


def _probe_config(cfg: Config, table: EffectiveTable) -> Config:
    """A Config that mirrors *cfg*'s policy (namespace, quotas, mode) but
    always allowlists :data:`PROBE_PROJECT` — every probe path is built
    against that project, so a probe exercises the entry's own checks, not
    an incidental R6 project-boundary deny (except the probes that are
    deliberately *about* the project boundary — see ``entries.py``'s
    ``issue.create`` probe, which targets ``OTHER_PROJECT`` instead).

    Also seeds ``effective_endpoints`` with *table* directly, the same way
    :class:`functools.cached_property` itself would (``instance.__dict__``) —
    ``guards.gitlab_api.policy.decide`` reads ``cfg.effective_endpoints``
    internally, and the startgate must validate *exactly* the table it was
    handed, not a table freshly rebuilt from ``cfg.endpoint_activation``. In
    the real boot path (``__main__.py``) these are the same table by
    construction; tests exercise ad-hoc tables that intentionally don't
    correspond to any real ``[api.endpoints]`` config at all (§04.4 — a probe
    must hold however the table was built).
    """
    probe_cfg = replace(cfg, allowed_projects=(PROBE_PROJECT,), allowed_project_ids=())
    probe_cfg.__dict__["effective_endpoints"] = table
    return probe_cfg


def _probe_intent(probe: DenyProbe) -> ApiIntent:
    project = _project_from_probe_path(probe.path)
    return ApiIntent(
        _project=project,
        _method=probe.method,
        path=probe.path,
        fields=dict(probe.fields),
        mr_owner_ok=probe.mr_owner_ok,
    )


def _run_probe(cfg: Config, entry_id: str, probe: DenyProbe) -> None:
    # Deferred import: ``..policy`` imports this catalog package (for the
    # entry/builtin tables), and this package's __init__ imports this module —
    # importing policy at module scope would close that loop during Python's
    # import bootstrap. By probe time both modules are long since loaded
    # (same pattern as ``core.config.effective_endpoints``).
    from ..policy import full_decide

    d = full_decide(_probe_intent(probe), StateView(), cfg)
    if d.allow:
        raise StartgateFailure(
            f"catalog entry {entry_id!r}: deny-probe {probe.description!r} "
            f"({probe.method} {probe.path}) was ALLOWED by the effective policy "
            "— refusing to start"
        )


def run_startgate(cfg: Config, table: EffectiveTable) -> None:
    """Run every activated entry's deny-probes, plus the built-in global
    probes, against the effective policy. Raises :class:`StartgateFailure`
    on the first probe that would be allowed.
    """
    probe_cfg = _probe_config(cfg, table)
    for entry in table.entries:
        for probe in entry.deny_probes:
            _run_probe(probe_cfg, entry.id, probe)
    for probe in BUILTIN_DENY_PROBES:
        _run_probe(probe_cfg, "<builtin>", probe)
