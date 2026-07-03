"""Capability-invariant layer: closed vocabulary, compiled-in deny set.

Every guard normalizes its intent to a small, closed vocabulary (what the request would *do*,
independent of transport: git ref-command, REST body field, SQL statement, …).
:meth:`core.guard.Guard.handle` checks against :data:`FORBIDDEN` before allow-logic:
if capabilities intersect it, the request is denied, regardless of endpoint row or ref-check.

The intent→capability *mapping* is guard-specific and lives in each guard's module.

This module is only as safe as the completeness of the mapping per guard — a much
smaller trust-critical surface than the scattered checks it replaces.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .model import Decision
from .rules import R4


class Capability(str, Enum):
    """What a request would do, abstracted from transport.

    Deliberately closed: deny-invariants must never become configurable.
    Adding a member is a code change, reviewed on this trust boundary.
    """

    CREATES_REF = "creates_ref"
    DELETES_REF = "deletes_ref"
    CREATES_TAG = "creates_tag"
    MERGES = "merges"
    ESCALATES_PRIVILEGE = "escalates_privilege"
    WRITES_OUTSIDE_NAMESPACE = "writes_outside_namespace"
    DESTROYS_DATA = "destroys_data"


FORBIDDEN: frozenset[Capability] = frozenset(
    {
        Capability.DELETES_REF,
        Capability.CREATES_TAG,
        Capability.MERGES,
        Capability.ESCALATES_PRIVILEGE,
        Capability.DESTROYS_DATA,
    }
)
"""Compiled in, **never** configurable (not even behind a flag).

Deliberately excludes ``creates_ref`` (agent's normal mode) and ``writes_outside_namespace``
(per-deployment setting in R2, not here).

``destroys_data`` and ``escalates_privilege`` have no producer today but are declared
so future guards (e.g. Postgres DDL/GRANT) extend an existing invariant.
"""


def forbidden_check(caps: frozenset[Capability]) -> Optional[Decision]:
    """Deny (R4) if ``caps`` hits ``FORBIDDEN``.

    Returns ``None`` when clear, same shape as per-endpoint/per-ref checks.
    Kernel runs this before any guard's allow-logic: a hit short-circuits.
    """
    hit = FORBIDDEN & caps
    if not hit:
        return None
    names = ", ".join(sorted(c.value for c in hit))
    return Decision(False, R4, f"forbidden capability: {names}")
