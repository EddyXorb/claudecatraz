"""Catalog data model: pure dataclasses a catalog entry is built from.

No policy logic lives here — see ``checks.py`` for the Check registry,
``entries.py`` for the table itself, and ``activation.py``/``startgate.py``
for how config turns a subset into what is matched against.
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

# Synthetic projects the startgate uses to build probe requests: one always
# allowlisted (so an entry's own checks are exercised, not R6), and one
# deliberately never allowlisted (to prove the project boundary applies everywhere).
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
    """Where a decision field lives on the wire."""

    BODY = "body"
    QUERY = "query"


@dataclass(frozen=True)
class FieldSpec:
    """One field a catalog entry's decision depends on, and where to read it.

    The guard extracts *only* the fields an entry declares here, each from its
    declared location (body or query) — never a blind merge. A field declared
    ``BODY`` that only shows up in the query string is simply absent from the
    decision, exactly as if the caller never sent it.
    """

    name: str
    location: Location = Location.BODY


CheckFn = Callable[[ApiIntent, StateView, Config], Optional[Decision]]


@dataclass(frozen=True)
class RegisteredCheck:
    """A named, parametrised check-registry entry.

    ``needs`` declares the check's data dependency — e.g. ``{"mr_owner"}`` for
    an ownership check — so a caller can decide whether to run an unpure lookup
    *by declared need*, not by comparing function identity against hardcoded predicates.
    """

    name: str
    fn: CheckFn
    needs: frozenset[str] = frozenset()

    def __call__(self, intent: ApiIntent, state: StateView, cfg: Config) -> Optional[Decision]:
        return self.fn(intent, state, cfg)


@dataclass(frozen=True)
class DenyProbe:
    """A must-deny example the catalog entry ships with itself.

    Owned by the catalog: the startgate runs every activated entry's probes
    against the effective policy at startup, and a probe that would be *allowed*
    aborts the boot.

    ``fields`` mirrors the entry's own body/query split loosely — the startgate
    builds an :class:`~warden.guards.gitlab_api.intent.ApiIntent` directly
    (no HTTP parsing), so it does not need to distinguish location: it sets
    ``intent.fields`` verbatim. ``mr_owner_ok`` covers probes for ownership-gated
    entries, where the datum is the out-of-band ownership lookup, not a wire field.
    """

    description: str
    method: str
    path: str
    fields: Mapping[str, object] = field(default_factory=dict)
    mr_owner_ok: Optional[bool] = None


@dataclass(frozen=True)
class CatalogEntry:
    """One row of the endpoint catalog — the unit a catalog PR adds.

    ``id`` is the stable name the activation config and CLI use (``mr.create``,
    ``branch.create``, …). It defaults to ``""`` only so ad-hoc rows built
    directly in tests keep working; every row in ``entries.CATALOG`` sets it.
    """

    method: str
    template: str
    checks: tuple[RegisteredCheck, ...]
    rule: str
    kind: EndpointKind
    capabilities: frozenset[Capability] = frozenset()
    id: str = ""
    decision_fields: tuple[FieldSpec, ...] = ()

    @functools.cached_property
    def regex(self) -> re.Pattern[str]:
        # {id}/{iid} → one non-slash, URL-encoded path segment (path_template).
        return compile_template(self.template)
