"""Catalog data model (§04.2, docs/design/architecture-generalization,
§04-policy-erweiterbarkeit.md §04.1/04.2): the pure dataclasses a catalog
entry is built from.

No policy logic lives here — see ``checks.py`` for the Check registry (§04.1),
``entries.py`` for the table itself, and ``activation.py``/``startgate.py``
for how config turns a subset of this table into what a request is actually
matched against.
"""

from __future__ import annotations

import functools
import re
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Final, Mapping, Optional

from ....core.capabilities import Capability
from ....core.config import Config
from ....core.model import Decision, StateView
from ....core.path_template import compile_template
from ..intent import ApiIntent

# Synthetic projects the startgate (§04.4) uses to build probe requests: one
# that the probe's Config always allowlists (so an entry's *own* checks are
# exercised, not R6), and one that is deliberately never allowlisted (so a
# probe can prove the project boundary itself still applies to every entry,
# including ones with no checks of their own — e.g. ``issue.create``).
PROBE_PROJECT: Final[str] = "probe/project"
PROBE_PROJECT_PATH: Final[str] = urllib.parse.quote(PROBE_PROJECT, safe="")
OTHER_PROJECT: Final[str] = "other/project"
OTHER_PROJECT_PATH: Final[str] = urllib.parse.quote(OTHER_PROJECT, safe="")


class EndpointKind(str, Enum):
    """What a write endpoint creates/touches — drives quota accounting (R5)."""

    MERGE = "merge"
    MR = "mr"
    NOTE = "note"
    MR_UPDATE = "mr_update"
    PIPELINE = "pipeline"
    BRANCH = "branch"
    ISSUE = "issue"


class Location(str, Enum):
    """Where a decision field lives on the wire (F12, §04.2)."""

    BODY = "body"
    QUERY = "query"


@dataclass(frozen=True)
class FieldSpec:
    """One field a catalog entry's decision depends on, and where to read it.

    F12 fix: the guard extracts *only* the fields an entry declares here,
    each from its declared location (body or query) — never a blind merge of
    both. A field declared ``BODY`` that only shows up in the query string (or
    vice versa) is simply absent from the decision, exactly as if the caller
    never sent it — closing the footgun where a scoping check could "pass" on
    a value the upstream request never actually carried.
    """

    name: str
    location: Location = Location.BODY


CheckFn = Callable[[ApiIntent, StateView, Config], Optional[Decision]]


@dataclass(frozen=True)
class RegisteredCheck:
    """A named, parametrised check-registry entry (§04.1, F2/F10).

    ``needs`` declares the check's data dependency — e.g. ``{"mr_owner"}`` for
    an ownership check — so a caller (``guards.gitlab_api.guard.ApiGuard.enrich``)
    can decide whether to run an unpure lookup *by declared need*, not by
    comparing function identity against a hardcoded predicate (F2's actual
    complaint: ``mr_owned_by_claude in ep.checks``).
    """

    name: str
    fn: CheckFn
    needs: frozenset[str] = frozenset()

    def __call__(self, intent: ApiIntent, state: StateView, cfg: Config) -> Optional[Decision]:
        return self.fn(intent, state, cfg)


@dataclass(frozen=True)
class DenyProbe:
    """A must-deny example the catalog entry ships with itself (§04.4).

    Owned by the catalog, not a herderless seed directory (Röst-Runde 2): the
    startgate runs every activated entry's probes against the effective
    policy at startup, and a probe that would be *allowed* aborts the boot.

    ``fields`` mirrors the entry's own body/query split loosely — the
    startgate builds an :class:`~warden.guards.gitlab_api.intent.ApiIntent`
    directly (no HTTP parsing involved), so it does not need to distinguish
    location: it sets ``intent.fields`` verbatim. ``mr_owner_ok`` covers
    probes for ownership-gated entries, where the datum being probed is not a
    wire field at all but the out-of-band ownership lookup (``None`` —
    unverifiable — is itself already a natural deny case for those entries).
    """

    description: str
    method: str
    path: str
    fields: Mapping[str, object] = field(default_factory=dict)
    mr_owner_ok: Optional[bool] = None


@dataclass(frozen=True)
class OverridableParam:
    """One override knob a catalog entry exposes (§04.2/04.3).

    ``rebuild`` produces the replacement check for a validated override
    value; ``is_narrower`` is the fail-closed proof that the new value cannot
    possibly grant more than the default, checked *before* ``rebuild`` runs.
    Both are plain functions of ``(Config, value)`` so a proof can lean on
    e.g. ``Config.in_branch_namespace`` instead of re-deriving the namespace
    rule for itself.
    """

    key: str
    check_index: int
    is_narrower: Callable[[Config, object], bool]
    rebuild: Callable[[Config, object], RegisteredCheck]


@dataclass(frozen=True)
class CatalogEntry:
    """One row of the endpoint catalog (§04.2) — the unit a catalog PR adds.

    ``id`` is the stable name the activation config and the CLI address this
    entry by (``mr.create``, ``branch.create``, …). It defaults to ``""``
    only so ad-hoc rows built directly in tests (pre-dating the catalog, e.g.
    a hypothetical row proving the capability layer is structural) keep
    working without naming a real entry; every row in ``entries.CATALOG``
    sets it.
    """

    method: str
    template: str
    checks: tuple[RegisteredCheck, ...]
    rule: str
    kind: EndpointKind
    capabilities: frozenset[Capability] = frozenset()
    id: str = ""
    decision_fields: tuple[FieldSpec, ...] = ()
    deny_probes: tuple[DenyProbe, ...] = ()
    overridable: tuple[OverridableParam, ...] = ()

    @functools.cached_property
    def regex(self) -> re.Pattern[str]:
        # {id}/{iid} → one non-slash, URL-encoded path segment (path_template).
        return compile_template(self.template)
