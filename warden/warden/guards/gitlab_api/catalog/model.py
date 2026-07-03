"""Catalog data model: pure dataclasses a Recognizer is built from.

No policy logic lives here — the one generic scope decision lives in
``policy.py``; ``entries.py``/``read_endpoints.py`` are the tables themselves;
``activation.py`` is how config turns a subset of the write table into what is
matched against.

§07 Punkt 7 unifies the former write-``CatalogEntry``/check-tuple shape and
the read-table's always-terminal ``ReadCheck`` shape into **one** type:
:class:`Recognizer`. A recognizer is metadata (``id``, ``method``,
``template``) plus a closed, normalized **scope** (:class:`ScopeKind`) the one
generic ``policy.decide_scope`` consumes — never a per-entry decision
function. The single exception is ``content-exposure`` (the read side),
where a genuinely per-row classification is unavoidable (e.g. ``/search``'s
verdict depends on its ``scope`` query field) — even there the function is
narrowly typed to return only a :class:`ReadClass` + reason, never an
arbitrary :class:`~warden.core.model.Decision`.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

from ....core.capabilities import Capability
from ....core.path_template import compile_template

if TYPE_CHECKING:  # avoids a load cycle (recognizers reference this module)
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
    """One field a recognizer's decision depends on, and where to read it.

    The guard extracts *only* the fields a recognizer declares here, each from its
    declared location (body or query) — never a blind merge. A field declared
    ``BODY`` that only shows up in the query string is simply absent from the
    decision, exactly as if the caller never sent it.
    """

    name: str
    location: Location = Location.BODY


class ScopeKind(str, Enum):
    """The closed scope vocabulary every recognizer's match reduces to (§07
    Punkt 7). Exactly three members — see the design doc's "geschlossener
    Scope-Raum":

    * ``BRANCH_NAMESPACE`` — a branch name (literal, from the request, or
      resolved via an iid → MR upstream lookup) must lie in the agent's
      configured namespace.
    * ``QUOTA_BY_KIND`` — no branch scope; only the project boundary (kernel)
      and this endpoint kind's quota apply.
    * ``CONTENT_EXPOSURE`` — the read side: projectless GETs, terminal
      metadata-allow/content-deny classification.
    """

    BRANCH_NAMESPACE = "branch-namespace"
    QUOTA_BY_KIND = "quota-by-kind"
    CONTENT_EXPOSURE = "content-exposure"


class ReadClass(str, Enum):
    """A content-exposure recognizer's terminal classification: metadata is
    always readable (R1), content is never readable projectless (R6)."""

    METADATA = "metadata"
    CONTENT = "content"


# A content-exposure recognizer's classifier: narrow by construction — it may
# only return a closed (:class:`ReadClass`, reason) pair, never an arbitrary
# Decision. This is the one place a per-row function is unavoidable (e.g.
# ``/search``'s verdict depends on the request's ``scope`` query field), kept
# as narrow as the branch-namespace/quota-by-kind rows' plain data fields.
ClassifyFn = Callable[["ApiIntent"], tuple[ReadClass, str]]


@dataclass(frozen=True)
class Recognizer:
    """One row of the endpoint table — read or write, same type (§07 Punkt 7).

    ``id`` is the stable name the activation config and CLI use (``mr.create``,
    ``branch.create``, …) for write recognizers; read recognizers carry one
    too (for debugging/audit) but it is not activation-addressable — the read
    table is not configurable, unlike the write catalog.

    ``scope_kind`` plus the scope-specific fields below are the *only* things
    the one generic ``policy.decide_scope`` consumes:

    * ``BRANCH_NAMESPACE``: ``namespace_field`` names the request field (body
      or query, per ``decision_fields``) holding the branch literally; ``None``
      means the request carries no branch directly (only an iid) — the branch
      is resolved via the iid → MR upstream lookup and lands in
      ``intent.mr_source_ok`` (tristate, populated by the guard's ``enrich``).
    * ``QUOTA_BY_KIND``: no extra field — project boundary + ``kind`` quota only.
    * ``CONTENT_EXPOSURE``: ``classify`` is the terminal metadata/content call.

    ``kind``/``rule``/``capabilities``/``decision_fields`` remain meaningful
    only for write recognizers (``BRANCH_NAMESPACE``/``QUOTA_BY_KIND``); a
    read recognizer leaves them at their defaults.
    """

    id: str
    method: str
    template: str
    scope_kind: ScopeKind
    kind: Optional[EndpointKind] = None
    rule: str = ""
    capabilities: frozenset[Capability] = frozenset()
    decision_fields: tuple[FieldSpec, ...] = ()
    namespace_field: Optional[str] = None
    classify: Optional[ClassifyFn] = None

    @functools.cached_property
    def regex(self) -> re.Pattern[str]:
        # {id}/{iid} → one non-slash, URL-encoded path segment (path_template).
        return compile_template(self.template)
