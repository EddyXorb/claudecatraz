"""The startgate (§04.4; docs/design/architecture-generalization,
§04-policy-erweiterbarkeit.md §04.4, §06-migration.md Schritt 4).

Policy-by-Example as a *gate*, not a mechanism: every activated catalog
entry's deny-probes, plus the built-in invariants' global probes, run against
the effective, pure policy (``policy.decide``) before the warden opens a
port. A probe that would be *allowed* means the code or config has a bug
serious enough to abort the boot — no request has ever been served with it.

No network, no state DB: probes run against a synthetic, always-unlocked
:class:`~warden.model.StateView` and a ``Config`` copy whose allowlist is
widened just enough to let each probe reach the entry's *own* checks (see
:data:`~warden.catalog.model.PROBE_PROJECT`) — a probe proving the project
boundary itself uses the deliberately-not-allowlisted
:data:`~warden.catalog.model.OTHER_PROJECT` instead.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import replace

from ..config import Config
from ..model import Channel, ProxyRequest, StateView
from ..policy import decide
from .activation import EffectiveTable
from .builtin import BUILTIN_DENY_PROBES
from .errors import StartgateFailure
from .model import PROBE_PROJECT, DenyProbe

# Mirrors api_proxy._project_from_path exactly — duplicated rather than
# imported to avoid a load-time cycle (api_proxy imports warden.catalog; this
# is a 2-line regex, not worth coupling the two modules over).
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
    ``policy.decide`` reads ``cfg.effective_endpoints`` internally, and the
    startgate must validate *exactly* the table it was handed, not a table
    freshly rebuilt from ``cfg.endpoint_activation``. In the real boot path
    (``__main__.py``) these are the same table by construction; tests exercise
    ad-hoc tables that intentionally don't correspond to any real
    ``[api.endpoints]`` config at all (§04.4 — a probe must hold however the
    table was built).
    """
    probe_cfg = replace(cfg, allowed_projects=(PROBE_PROJECT,), allowed_project_ids=())
    probe_cfg.__dict__["effective_endpoints"] = table
    return probe_cfg


def _probe_request(probe: DenyProbe) -> ProxyRequest:
    project = _project_from_probe_path(probe.path)
    req = ProxyRequest(
        channel=Channel.API,
        project=project,
        method=probe.method,
        path=probe.path,
        fields=dict(probe.fields),
    )
    req.mr_owner_ok = probe.mr_owner_ok
    return req


def _run_probe(cfg: Config, entry_id: str, probe: DenyProbe) -> None:
    d = decide(_probe_request(probe), StateView(), cfg)
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
