"""Catalog data model: pure dataclasses a catalog entry is built from.

No policy logic lives here — see ``checks.py`` for the Check registry,
``entries.py`` for the table itself, and ``activation.py`` for how config
turns a subset into what is matched against.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from ....core.capabilities import Capability
from ....core.config import Config
from ....core.model import Decision, StateView
from ....core.path_template import compile_template
from ..intent import ApiIntent


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
